/// <reference types="@tarojs/taro" />

declare module '*.png';
declare module '*.gif';
declare module '*.jpg';
declare module '*.jpeg';
declare module '*.svg';
declare module '*.css';
declare module '*.less';
declare module '*.scss';
declare module '*.sass';
declare module '*.styl';

/** Build-time transport constants projected by config/runtime-env.ts. */
declare const __FITNESS_API_BASE_URL__: string
declare const __FITNESS_CLOUD_ENV__: string
declare const __FITNESS_CLOUD_SERVICE__: string
declare const __FITNESS_BUILD_VERSION__: string
declare const __FITNESS_BUILD_COMMIT__: string
declare const __FITNESS_BUILD_SOURCE_DIRTY__: boolean

declare namespace NodeJS {
  interface ProcessEnv {
    /** NODE 内置环境变量, 会影响到最终构建生成产物 */
    NODE_ENV: 'development' | 'production',
    /** 当前构建的平台 */
    TARO_ENV: 'weapp' | 'swan' | 'alipay' | 'h5' | 'rn' | 'tt' | 'qq' | 'jd' | 'harmony' | 'jdrn'
    /**
     * 当前构建的小程序 appid
     * @description 若不同环境有不同的小程序，可通过在 env 文件中配置环境变量`TARO_APP_ID`来方便快速切换 appid， 而不必手动去修改 dist/project.config.json 文件
     * @see https://taro-docs.jd.com/docs/next/env-mode-config#特殊环境变量-taro_app_id
     */
    TARO_APP_ID: string
    /** Fitness Agent 后端 API 根地址，包含 /api/v1。 */
    TARO_APP_API_BASE_URL?: string
    /** 微信云托管环境 ID；与服务名同时存在时启用 callContainer。 */
    TARO_APP_CLOUD_ENV?: string
    /** 微信云托管服务名；与环境 ID 同时存在时启用 callContainer。 */
    TARO_APP_CLOUD_SERVICE?: string
    /** Traceable mini program build version supplied by the build wrapper. */
    TARO_APP_BUILD_VERSION?: string
    /** Full Git SHA supplied by the build wrapper. */
    TARO_APP_BUILD_COMMIT?: string
    /** Whether tracked or untracked source changes existed at build time. */
    TARO_APP_BUILD_SOURCE_DIRTY?: 'true' | 'false'
  }
}
