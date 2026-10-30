import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/api";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";

/**
 * "Google 로 계속하기" 버튼 (Google Identity Services).
 * 서버(/auth/google/config)에 클라이언트 ID 가 설정돼 있을 때만 그린다. 버튼이 준 ID 토큰을 서버에 넘기면
 * 서버가 검증하고 처음 온 사람은 바로 가입시킨다 — 로그인·회원가입 화면 모두 같은 버튼을 쓴다.
 */
interface GoogleId {
  initialize(o: { client_id: string; callback: (r: { credential?: string }) => void; ux_mode?: "popup"; auto_select?: boolean; itp_support?: boolean }): void;
  renderButton(el: HTMLElement, o: Record<string, unknown>): void;
}
declare global {
  interface Window {
    google?: { accounts: { id: GoogleId } };
  }
}

const GSI_SRC = "https://accounts.google.com/gsi/client";
let gsiPromise: Promise<GoogleId> | null = null;
function loadGsi(): Promise<GoogleId> {
  if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id);
  if (!gsiPromise) {
    gsiPromise = new Promise<GoogleId>((resolve, reject) => {
      const s = document.createElement("script");
      s.src = GSI_SRC;
      s.async = true;
      s.onload = () => (window.google?.accounts?.id ? resolve(window.google.accounts.id) : reject(new Error("gsi")));
      s.onerror = () => reject(new Error("gsi"));
      document.head.appendChild(s);
    }).catch((e) => {
      gsiPromise = null; // 다음에 창을 열 때 다시 시도
      throw e;
    });
  }
  return gsiPromise;
}

/** 설정 조회는 한 번만 — 창을 열고 닫을 때마다 다시 묻지 않는다 */
let configPromise: Promise<string | null> | null = null;
function googleClientId(): Promise<string | null> {
  configPromise ??= api.auth
    .googleConfig()
    .then((c) => (c.enabled && c.clientId ? c.clientId : null))
    .catch(() => {
      configPromise = null;
      return null;
    });
  return configPromise;
}

export default function GoogleLogin() {
  const loginWithGoogle = useSession((s) => s.loginWithGoogle);
  const closeAuth = useUi((s) => s.closeAuth);
  const toast = useUi((s) => s.toast);
  const box = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      const clientId = await googleClientId();
      if (!clientId || !alive) return;
      const gid = await loadGsi().catch(() => null);
      if (!gid || !alive || !box.current) return;
      gid.initialize({
        client_id: clientId,
        ux_mode: "popup",
        itp_support: true,
        callback: async ({ credential }) => {
          if (!credential) return;
          setBusy(true);
          setErr(null);
          try {
            await loginWithGoogle(credential);
            closeAuth();
            toast("Google 계정으로 로그인했습니다.", "success");
          } catch (x) {
            setErr(x instanceof ApiError ? x.message : "Google 로그인에 실패했습니다. 잠시 후 다시 시도해 주세요.");
            setBusy(false);
          }
        },
      });
      gid.renderButton(box.current, { type: "standard", theme: "filled_black", size: "large", shape: "pill", text: "continue_with", locale: "ko", width: Math.min(box.current.clientWidth || 320, 400) });
      setReady(true);
    })();
    return () => {
      alive = false;
    };
  }, [loginWithGoogle, closeAuth, toast]);

  return (
    <div className="google-login" hidden={!ready} aria-busy={busy}>
      <div className="or-line">
        <span>또는</span>
      </div>
      <div ref={box} className="google-btn" />
      {busy && <div className="hint" style={{ textAlign: "center" }}>Google 계정 확인 중…</div>}
      {err && <div className="form-err">{err}</div>}
      <p className="legal-note">
        계속하면 <a href="/terms" target="_blank" rel="noreferrer">이용약관</a>과{" "}
        <a href="/privacy" target="_blank" rel="noreferrer">개인정보처리방침</a>에 동의하는 것으로 봅니다.
      </p>
    </div>
  );
}
