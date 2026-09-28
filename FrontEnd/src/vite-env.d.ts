/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 백엔드 기본 경로. 기본 /api (개발 서버는 vite.config.ts 프록시, 배포는 호스트 Nginx 가 Spring 으로 넘긴다) */
  readonly VITE_API_BASE?: string;
  /** 개발 서버 프록시 대상 (vite.config.ts 에서만 읽는다) */
  readonly VITE_API_PROXY?: string;
}
