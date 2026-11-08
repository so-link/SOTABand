/**
 * 密码规则的**前端唯一副本**。
 *
 * ⚠️ 真正的裁决在服务端（`core/auth/passwords.py`）。这里只做即时反馈，
 * 让用户不用点提交就知道哪条没满足。
 *
 * ## 为什么抽成模块
 *
 * 注册页和改密码弹窗都要用同一套规则。以前规则写死在 `LoginView` 里，
 * 加改密码功能时就会出现两份副本 —— 改一处忘一处，用户会看到"注册时说合规、
 * 改密码时说不合规"这种自相矛盾的提示。
 *
 * 与服务端保持同源的三条：
 * 1. 长度 **8–20 位**（按字符数，不是字节）
 * 2. 含**字母**
 * 3. 含**数字**
 */

export const PASSWORD_MIN_LENGTH = 8
export const PASSWORD_MAX_LENGTH = 20

export interface PasswordChecks {
  length: boolean
  letter: boolean
  digit: boolean
}

/** 与服务端 `validate_password` 等价的规则判定。 */
export function checkPassword(value: string): PasswordChecks {
  return {
    length: value.length >= PASSWORD_MIN_LENGTH && value.length <= PASSWORD_MAX_LENGTH,
    // \p{L} = Unicode 字母，与服务端的 [^\W\d_] 等价
    letter: /\p{L}/u.test(value),
    // \p{Nd} = Unicode 十进制数字，与服务端的 \d(UNICODE) 等价
    digit: /\p{Nd}/u.test(value),
  }
}

/** 给出**第一条**不满足的规则的用户提示；全部满足返回 null。 */
export function describePasswordProblem(value: string): string | null {
  const c = checkPassword(value)
  if (!c.length) {
    return `密码长度需为 ${PASSWORD_MIN_LENGTH}-${PASSWORD_MAX_LENGTH} 位，当前 ${value.length} 位`
  }
  if (!c.letter) return '密码需包含至少一个字母'
  if (!c.digit) return '密码需包含至少一个数字'
  return null
}
