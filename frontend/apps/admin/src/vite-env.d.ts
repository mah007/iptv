/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Build-time environment label: development | staging | production. */
  readonly VITE_APP_ENV?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
