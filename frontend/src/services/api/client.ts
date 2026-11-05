/**
 * 统一 API 客户端 —— 全部 HTTP 请求的唯一入口。
 *
 * ## 为什么需要它
 *
 * 改造前项目里有 **89 处裸 `fetch()`** 散落在 26 个文件里，没有一处设置
 * `credentials`，也没有统一的 401/403 处理。认证一旦落地，这种散装调用会出现
 * 三个问题：
 *
 * 1. Cookie 不随请求发送 → 永远"未登录"；
 * 2. 每处各写一遍 `if (!res.ok)` → 错误处理规则漂移；
 * 3. 401 没人统一处理 → 会话过期后前端卡在空白页。
 *
 * ## 分层
 *
 * ```text
 * apiFetchRaw   ← 最底层：发请求、带 Cookie、401 回调、网络错误包装。返回 Response
 *   ├─ apiFetch ← 解析 JSON（业务代码默认用这个）
 *   ├─ apiText  ← 返回纯文本
 *   └─ apiBlob  ← 返回二进制（下载/预览）
 *
 * apiGet / apiPost / apiPatch / apiDelete  ← JSON 语义的便捷封装
 * ```
 *
 * ## 关键约定
 *
 * - `credentials: 'include'` —— 会话 Cookie 随请求发送；
 * - BASE_URL 保持 `''`（相对路径）—— 开发期走 Vite 的 `/api` 代理，生产期由
 *   Nginx 反代（ADR-08-05），两种情况都不需要跨域；
 * - 401 触发**一次**全局回调（由 auth store 注册），用来把状态机切到 `expired`。
 *   登录/注册/改密码接口要传 `skipAuthRedirect` —— 它们的 401/400 是业务失败，
 *   不是"会话过期"。
 * - `signal`（AbortController）会透传给 `fetch` —— 参数里 `...rest` 已包含它。
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

/**
 * 拼出可直接放进 `src` / `href` 的接口地址。
 *
 * 图片预览、文件下载这类**不走 fetch** 的用途需要完整 URL（浏览器自己去请求，
 * 没法经过 `apiFetch`）。让 base URL 只存在这一个地方，避免组件里再出现
 * `const BASE_URL = ''` 的副本。
 *
 * ⚠️ 这类请求浏览器**不会**带自定义头，也不会经过 401 回调 —— 它们是同源
 * （开发期走 Vite 代理、生产期走 Nginx 反代），Cookie 会自动带上。
 */
export function apiUrl(path: string): string {
  return `${BASE_URL}${path}`
}

/** 带 HTTP 语义的错误对象。组件按 `status` 决定提示，不要解析 message。 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  /** 后端回写的 request_id，反馈问题时用得上 */
  readonly requestId?: string

  constructor(status: number, detail: string, requestId?: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.requestId = requestId
  }

  /** 未认证 / 会话过期 */
  get isUnauthorized() {
    return this.status === 401
  }

  /** 已认证但无权限（不重试、不刷新） */
  get isForbidden() {
    return this.status === 403
  }

  /** 不存在，或存在但无权知晓其存在（不泄露存在性） */
  get isNotFound() {
    return this.status === 404
  }
}

// ── 全局 401 回调 ─────────────────────────────────────────────────────

type UnauthorizedHandler = () => void

let unauthorizedHandler: UnauthorizedHandler | null = null

/** 由 auth store 注册，用于在会话失效时切换状态机。 */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null) {
  unauthorizedHandler = handler
}

// ── 请求 ──────────────────────────────────────────────────────────────

export interface ApiFetchOptions extends RequestInit {
  /** 跳过全局 401 回调（登录/注册等"401 表示业务失败"的接口要设 true） */
  skipAuthRedirect?: boolean
}

function extractDetail(data: unknown): string | undefined {
  if (!data || typeof data !== 'object') return undefined
  const detail = (data as { detail?: unknown }).detail

  if (typeof detail === 'string') return detail

  // FastAPI 的 422 校验错误是 [{loc, msg, type}, ...]
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string'
          ? (item as { msg: string }).msg
          : null
      )
      .filter((m): m is string => Boolean(m))
    return messages.length ? messages.join('；') : undefined
  }

  return undefined
}

function isFormData(body: BodyInit | null | undefined): boolean {
  return typeof FormData !== 'undefined' && body instanceof FormData
}

/**
 * 发一个请求，**返回原始 Response**。
 *
 * 负责统一的三件事：`credentials: 'include'`、FormData 的 Content-Type 处理、
 * 401 全局回调、网络层错误的 ApiError 包装。**不解析响应体**。
 *
 * 只有需要非 JSON 响应的调用方才该用它（`text()` / `blob()` / 读响应头）。
 * 一般业务代码用 {@link apiFetch}、{@link apiText}、{@link apiBlob}。
 */
export async function apiFetchRaw(
  path: string,
  options: ApiFetchOptions = {}
): Promise<Response> {
  const { skipAuthRedirect, headers, body, ...rest } = options

  const requestHeaders: Record<string, string> = {
    ...(headers as Record<string, string> | undefined),
  }
  // FormData 必须让浏览器自己设置带 boundary 的 Content-Type，不能手动写
  if (body != null && !isFormData(body) && !requestHeaders['Content-Type']) {
    requestHeaders['Content-Type'] = 'application/json'
  }

  let res: Response
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      credentials: 'include',
      ...rest,
      body,
      headers: requestHeaders,
    })
  } catch (err) {
    // AbortError 要原样抛：调用方用它区分"用户取消"和"真的失败"
    if (err instanceof Error && err.name === 'AbortError') throw err
    // 网络层失败（后端没起、断网）。与 HTTP 错误区分开，便于给出"连接异常"提示。
    throw new ApiError(0, '无法连接服务器，请检查网络或后端是否已启动')
  }

  if (res.status === 401 && !skipAuthRedirect) {
    unauthorizedHandler?.()
  }

  return res
}

/**
 * 发一个请求并解析 JSON。
 *
 * - `204` 或空响应体 → 返回 `undefined`；
 * - 非 2xx → 抛 {@link ApiError}；
 * - 401 且未跳过回调 → 先通知 auth store。
 */
export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {}
): Promise<T> {
  const res = await apiFetchRaw(path, options)
  const requestId = res.headers.get('X-Request-ID') ?? undefined

  if (res.status === 204) {
    if (!res.ok) throw new ApiError(res.status, res.statusText || '请求失败', requestId)
    return undefined as T
  }

  const text = await res.text()
  let data: unknown = undefined
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      // 非 JSON 响应（后端异常时可能出现 HTML 错误页）
      if (!res.ok) throw new ApiError(res.status, text.slice(0, 200), requestId)
      throw new ApiError(res.status, '响应格式不是 JSON', requestId)
    }
  }

  if (!res.ok) {
    throw new ApiError(
      res.status,
      extractDetail(data) ?? res.statusText ?? '请求失败',
      requestId
    )
  }

  return data as T
}

/** 发请求并返回**纯文本**响应体。非 2xx 抛 ApiError。 */
export async function apiText(
  path: string,
  options: ApiFetchOptions = {}
): Promise<string> {
  const res = await apiFetchRaw(path, options)
  const requestId = res.headers.get('X-Request-ID') ?? undefined
  const text = await res.text()
  if (!res.ok) {
    throw new ApiError(
      res.status,
      text ? text.slice(0, 200) : res.statusText || '请求失败',
      requestId
    )
  }
  return text
}

/** 发请求并返回**二进制**响应体（文件下载/预览）。非 2xx 抛 ApiError。 */
export async function apiBlob(
  path: string,
  options: ApiFetchOptions = {}
): Promise<Blob> {
  const res = await apiFetchRaw(path, options)
  if (!res.ok) {
    throw new ApiError(
      res.status,
      res.statusText || '请求失败',
      res.headers.get('X-Request-ID') ?? undefined
    )
  }
  return res.blob()
}

/**
 * 断言原始响应是 2xx；否则抛 {@link ApiError}（尽量解析出后端 detail）。
 *
 * **每个用 `apiFetchRaw` 的地方都必须先调它** —— `apiFetchRaw` 只包装网络层错误，
 * 4xx/5xx 会原样返回。流式接口若忘了这一步，错误响应会被当作 SSE 数据流解析，
 * 最终报一句毫无信息量的"未收到结果"，排查时非常痛苦。
 */
export async function assertOk(res: Response): Promise<void> {
  if (res.ok) return

  const requestId = res.headers.get('X-Request-ID') ?? undefined
  // 克隆一份再读：调用方可能还要读 body（虽然非 2xx 一般不需要）
  let detail: string | undefined
  try {
    const text = await res.clone().text()
    if (text) {
      try {
        detail = extractDetail(JSON.parse(text))
      } catch {
        detail = text.slice(0, 200)
      }
    }
  } catch {
    // 读 body 失败就退回状态文本
  }

  throw new ApiError(res.status, detail ?? res.statusText ?? '请求失败', requestId)
}

/** `apiFetch` 的便捷封装：JSON GET */
export function apiGet<T = unknown>(path: string, options?: ApiFetchOptions): Promise<T> {
  return apiFetch<T>(path, { method: 'GET', ...options })
}

/** `apiFetch` 的便捷封装：JSON POST */
export function apiPost<T = unknown>(
  path: string,
  payload?: unknown,
  options?: ApiFetchOptions
): Promise<T> {
  return apiFetch<T>(path, {
    method: 'POST',
    body: payload === undefined ? undefined : JSON.stringify(payload),
    ...options,
  })
}

/** `apiFetch` 的便捷封装：JSON PATCH */
export function apiPatch<T = unknown>(
  path: string,
  payload?: unknown,
  options?: ApiFetchOptions
): Promise<T> {
  return apiFetch<T>(path, {
    method: 'PATCH',
    body: payload === undefined ? undefined : JSON.stringify(payload),
    ...options,
  })
}

/** `apiFetch` 的便捷封装：DELETE（后端多数返回 204，解析出 undefined） */
export function apiDelete<T = unknown>(
  path: string,
  options?: ApiFetchOptions
): Promise<T> {
  return apiFetch<T>(path, { method: 'DELETE', ...options })
}

/** 上传 FormData（不设 Content-Type，让浏览器补 boundary）。 */
export function apiUpload<T = unknown>(
  path: string,
  form: FormData,
  options?: ApiFetchOptions
): Promise<T> {
  return apiFetch<T>(path, { method: 'POST', body: form, ...options })
}

/** 把任意异常转成可展示给用户的一句话。 */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return err.detail
    if (err.status === 401) return '邮箱或密码不正确'
    if (err.status === 403) return '权限不足'
    if (err.status === 404) return '资源不存在'
    if (err.status === 429) return '操作过于频繁，请稍后再试'
    return err.detail || `请求失败（HTTP ${err.status}）`
  }
  if (err instanceof Error) return err.message
  return '未知错误'
}
