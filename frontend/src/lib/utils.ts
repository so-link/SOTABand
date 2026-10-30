import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** 应用版本号：顶栏与状态栏统一从这里取，避免两处显示不一致 */
export const APP_VERSION = '0.1.0'
