import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { API_MODE, ApiError, api } from "@/api";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { Icon } from "@/components/ui";

/** 로그인 / 회원가입 모달. 화면 이동 없이 은하 위에서 처리한다. */
export default function AuthDialog() {
  const kind = useUi((s) => s.authDialog);
  const close = useUi((s) => s.closeAuth);
  useEffect(() => {
    if (!kind) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [kind, close]);
  if (!kind) return null;
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && close()} role="presentation">
      <div className="card dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <div className="row between">
          <span className="kick">{kind === "login" ? "sign in" : "create account"}</span>
          <button type="button" className="btn btn-g btn-xs" onClick={close} aria-label="닫기">
            <Icon.Close />
          </button>
        </div>
        {kind === "login" ? <LoginForm /> : <SignupForm />}
      </div>
    </div>
  );
}

/* ------------------------------ 검증 ------------------------------ */
/** 백엔드(SignupRequest)의 제약을 그대로 옮긴다 — 서버에 가기 전에 같은 판정을 낸다 */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const GENERIC_ERROR = "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";
const OFFLINE_ERROR = "로그인 서비스에 아직 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";

function validateEmail(value: string): string | null {
  const v = value.trim();
  if (!v) return "이메일을 입력해 주세요.";
  if (!EMAIL_RE.test(v)) return "이메일 형식이 올바르지 않습니다.";
  if (v.length > 255) return "이메일은 255자 이하여야 합니다.";
  return null;
}
/** 서버는 닉네임을 trim 하지 않으므로 프론트에서 다듬어 보내고, 검증도 다듬은 값으로 한다 */
function validateNickname(value: string): string | null {
  const v = value.trim();
  if (!v) return "닉네임을 입력해 주세요.";
  if (v.length < 2 || v.length > 30) return "닉네임은 2자 이상 30자 이하여야 합니다.";
  return null;
}
function validatePassword(value: string): string | null {
  if (!value) return "비밀번호를 입력해 주세요.";
  if (value.length < 8 || value.length > 64) return "비밀번호는 8자 이상 64자 이하여야 합니다.";
  if (!/[A-Za-z]/.test(value) || !/\d/.test(value)) return "비밀번호는 영문과 숫자를 각각 1자 이상 포함해야 합니다.";
  return null;
}

/** 서버가 내려준 fieldErrors 를 우리가 아는 필드에만 나눠 담는다 */
function pickFieldErrors(err: ApiError, fields: readonly string[]) {
  const map: Record<string, string> = {};
  err.fieldErrors.forEach((f) => {
    if (fields.includes(f.field) && !map[f.field]) map[f.field] = f.message;
  });
  return map;
}

/* ------------------------------ 공용 조각 ------------------------------ */
function EyeToggle({ shown, onToggle }: { shown: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="eye-btn" onClick={onToggle} aria-label={shown ? "비밀번호 숨기기" : "비밀번호 표시"} aria-pressed={shown} tabIndex={-1}>
      {shown ? <Icon.EyeOff /> : <Icon.Eye />}
    </button>
  );
}

type Avail = "unknown" | "checking" | "yes" | "no";
/**
 * 이메일·닉네임 중복 확인. 결과를 "확인한 값"과 함께 들고 있어서 값이 한 글자라도 바뀌면
 * 이전 판정이 자동으로 무효가 된다(오래된 "사용 가능"이 살아남지 않는다). 호출은 400ms 뒤,
 * 늦게 도착한 응답은 effect 정리로 버린다. 같은 값은 다시 확인하지 않는다.
 */
function useAvailability(kind: "email" | "nickname", value: string, valid: boolean) {
  // available: null 은 확인 실패 — 아무것도 보여 주지 않고 최종 판정은 서버(가입 요청)에 맡긴다
  const [result, setResult] = useState<{ value: string; available: boolean | null } | null>(null);
  useEffect(() => {
    if (!valid) return;
    let alive = true;
    const t = setTimeout(() => {
      const call = kind === "email" ? api.auth.checkEmail(value) : api.auth.checkNickname(value);
      call
        .then((r) => {
          if (alive) setResult({ value, available: r.available });
        })
        .catch(() => {
          if (alive) setResult({ value, available: null });
        });
    }, 400);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [kind, value, valid]);

  const state: Avail = !valid
    ? "unknown"
    : result?.value !== value
      ? "checking"
      : result.available === null
        ? "unknown"
        : result.available
          ? "yes"
          : "no";
  /** 서버가 409 로 중복을 알려 줬을 때 현재 값에 대한 판정을 덮어쓴다 */
  const markTaken = () => setResult({ value, available: false });
  return [state, markTaken] as const;
}

function AvailNote({ state }: { state: Avail }) {
  if (state === "unknown") return null;
  if (state === "checking") return <span className="avail">확인 중…</span>;
  return <span className={state === "yes" ? "avail yes" : "avail no"}>{state === "yes" ? "사용 가능" : "이미 사용 중"}</span>;
}

/* ------------------------------ 로그인 ------------------------------ */
const REMEMBER_KEY = "cosmos.auth.email";
function readRemembered() {
  try {
    return localStorage.getItem(REMEMBER_KEY) ?? "";
  } catch {
    return "";
  }
}
function writeRemembered(email: string | null) {
  try {
    if (email) localStorage.setItem(REMEMBER_KEY, email);
    else localStorage.removeItem(REMEMBER_KEY);
  } catch {
    /* 저장 실패는 로그인 흐름을 막지 않는다 */
  }
}

function LoginForm() {
  const login = useSession((s) => s.login);
  const openAuth = useUi((s) => s.openAuth);
  const closeAuth = useUi((s) => s.closeAuth);
  const toast = useUi((s) => s.toast);
  const prefill = useUi((s) => s.authPrefill);

  // 기억해 둔 이메일은 첫 렌더에 한 번만 읽는다
  const [remembered] = useState(readRemembered);
  const initialEmail = prefill?.email ?? remembered;
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(Boolean(remembered));
  const [showPw, setShowPw] = useState(false);
  const [caps, setCaps] = useState(false);
  const [busy, setBusy] = useState(false);
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [formErr, setFormErr] = useState<string | null>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const pwRef = useRef<HTMLInputElement>(null);

  const onCaps = (e: ReactKeyboardEvent<HTMLInputElement>) => setCaps(e.getModifierState?.("CapsLock") ?? false);
  const clearErrors = () => {
    if (formErr) setFormErr(null);
    if (Object.keys(fieldErr).length) setFieldErr({});
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setFormErr(null);
    setFieldErr({});
    const normalized = email.trim().toLowerCase();
    try {
      await login(normalized, password);
      writeRemembered(remember ? normalized : null);
      closeAuth();
      toast("로그인했습니다. 관심 기업과 스크랩이 반영됩니다.", "success");
    } catch (x) {
      if (x instanceof ApiError) {
        const fields = pickFieldErrors(x, ["email", "password"]);
        if (x.status === 400 && Object.keys(fields).length) {
          setFieldErr(fields);
          if (fields.email) emailRef.current?.focus();
          else pwRef.current?.focus();
        } else if (x.status === 404 || x.status === 405) {
          // 로그인 엔드포인트가 아직 배포되지 않은 경우
          setFormErr(OFFLINE_ERROR);
        } else {
          setFormErr(x.message);
        }
      } else {
        setFormErr(GENERIC_ERROR);
      }
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <h2 id="auth-title">다시 만나서 반가워요</h2>
      <p className="hint">로그인하면 관심 기업, 뉴스 스크랩, 내 은하를 사용할 수 있습니다.</p>
      <div className="form">
        {prefill?.notice && <div className="form-note">{prefill.notice}</div>}
        <label className="field">
          <span className="sr-only">이메일</span>
          <input
            ref={emailRef}
            type="email"
            placeholder="이메일"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              clearErrors();
            }}
            autoComplete="email"
            autoFocus={!initialEmail}
            aria-invalid={Boolean(fieldErr.email)}
            aria-describedby={fieldErr.email ? "login-email-err" : undefined}
          />
        </label>
        {fieldErr.email && (
          <div className="field-err" id="login-email-err">
            {fieldErr.email}
          </div>
        )}
        <label className="field">
          <span className="sr-only">비밀번호</span>
          <input
            ref={pwRef}
            type={showPw ? "text" : "password"}
            placeholder="비밀번호"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              clearErrors();
            }}
            onKeyDown={onCaps}
            onKeyUp={onCaps}
            autoComplete="current-password"
            autoFocus={Boolean(initialEmail)}
            aria-invalid={Boolean(fieldErr.password)}
            aria-describedby={fieldErr.password ? "login-pw-err" : undefined}
          />
          <EyeToggle shown={showPw} onToggle={() => setShowPw((v) => !v)} />
        </label>
        {fieldErr.password && (
          <div className="field-err" id="login-pw-err">
            {fieldErr.password}
          </div>
        )}
        {caps && <div className="caps-hint">Caps Lock이 켜져 있습니다</div>}
        <label className="remember">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          이메일 기억
        </label>
        {formErr && <div className="form-err">{formErr}</div>}
        <button type="submit" className="btn btn-p" disabled={busy || !email || !password} style={{ padding: 12 }}>
          {busy ? "확인 중…" : "로그인"}
        </button>
        <div className="hint" style={{ textAlign: "center" }}>
          아직 계정이 없나요?{" "}
          <button type="button" className="link-btn" onClick={() => openAuth("signup")}>
            회원가입
          </button>
        </div>
        {API_MODE === "mock" && (
          <div className="hint" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
            체험 계정 · <code>demo@cosmos.dev</code> / <code>cosmos123</code>
          </div>
        )}
      </div>
    </form>
  );
}

/* ------------------------------ 회원가입 ------------------------------ */
type SignupField = "email" | "nickname" | "password" | "passwordConfirm";
const SIGNUP_ORDER: readonly SignupField[] = ["email", "nickname", "password", "passwordConfirm"];

function SignupForm() {
  const signup = useSession((s) => s.signup);
  const openAuth = useUi((s) => s.openAuth);
  const closeAuth = useUi((s) => s.closeAuth);
  const toast = useUi((s) => s.toast);

  const [email, setEmail] = useState("");
  const [nickname, setNickname] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [touched, setTouched] = useState<Partial<Record<SignupField, boolean>>>({});
  const [serverErr, setServerErr] = useState<Partial<Record<SignupField, string>>>({});
  const [formErr, setFormErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const emailRef = useRef<HTMLInputElement>(null);
  const nicknameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);
  /** 제출 실패 시 첫 번째 문제 필드로 보낸다 */
  const focusField = (f: SignupField) => {
    const el = f === "email" ? emailRef.current : f === "nickname" ? nicknameRef.current : f === "password" ? passwordRef.current : confirmRef.current;
    el?.focus();
    el?.scrollIntoView({ block: "nearest" });
  };

  const emailError = validateEmail(email);
  const nicknameError = validateNickname(nickname);
  const passwordError = validatePassword(password);
  const confirmError = !confirm ? "비밀번호를 한 번 더 입력해 주세요." : confirm !== password ? "비밀번호가 일치하지 않습니다." : null;
  const clientError: Record<SignupField, string | null> = {
    email: emailError,
    nickname: nicknameError,
    password: passwordError,
    passwordConfirm: confirmError,
  };
  /** 서버가 돌려준 오류가 먼저, 그다음 blur/제출로 드러난 클라이언트 오류 */
  const shownError = (f: SignupField) => serverErr[f] ?? (touched[f] ? clientError[f] : null);

  const [emailAvail, markEmailTaken] = useAvailability("email", email.trim(), emailError === null);
  const [nickAvail, markNickTaken] = useAvailability("nickname", nickname.trim(), nicknameError === null);

  const markTouched = (f: SignupField) => setTouched((t) => ({ ...t, [f]: true }));
  const clearServer = (f: SignupField) => setServerErr((s) => (s[f] ? { ...s, [f]: undefined } : s));

  const rules = {
    len: password.length >= 8,
    letter: /[A-Za-z]/.test(password),
    digit: /\d/.test(password),
    tooLong: password.length > 64,
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setTouched({ email: true, nickname: true, password: true, passwordConfirm: true });
    setServerErr({});
    setFormErr(null);
    const firstBad = SIGNUP_ORDER.find((f) => clientError[f]);
    if (firstBad) {
      focusField(firstBad);
      return;
    }
    setBusy(true);
    const nick = nickname.trim();
    try {
      const { autoLogin } = await signup(email.trim(), password, nick);
      if (autoLogin) {
        closeAuth();
        toast(`환영합니다, ${nick}님.`, "success");
      } else {
        // 가입은 됐지만 자동 로그인을 못 한 경우 — 이메일을 채운 로그인 폼으로 넘긴다
        openAuth("login", { email: email.trim(), notice: "가입이 완료되었습니다. 로그인해 주세요." });
      }
    } catch (x) {
      if (x instanceof ApiError) {
        const fields = pickFieldErrors(x, ["email", "nickname", "password"]);
        if (x.code === "EMAIL_DUPLICATED") {
          setServerErr({ email: x.message });
          markEmailTaken();
          focusField("email");
        } else if (x.code === "NICKNAME_DUPLICATED") {
          setServerErr({ nickname: x.message });
          markNickTaken();
          focusField("nickname");
        } else if (x.status === 400 && Object.keys(fields).length) {
          setServerErr(fields);
          const bad = SIGNUP_ORDER.find((f) => fields[f]);
          if (bad) focusField(bad);
        } else {
          setFormErr(x.message);
        }
      } else {
        setFormErr(GENERIC_ERROR);
      }
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <h2 id="auth-title">탐사대에 합류하세요</h2>
      <p className="hint">이메일과 닉네임으로 가입합니다. 비밀번호는 8~64자, 영문과 숫자를 각 1자 이상 포함합니다.</p>
      <div className="form">
        <label className="field">
          <span className="sr-only">이메일</span>
          <input
            ref={emailRef}
            type="email"
            placeholder="이메일"
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              clearServer("email");
            }}
            onBlur={() => markTouched("email")}
            autoComplete="email"
            autoFocus
            maxLength={255}
            aria-invalid={Boolean(shownError("email"))}
            aria-describedby={shownError("email") ? "signup-email-err" : undefined}
          />
          <AvailNote state={emailAvail} />
        </label>
        {shownError("email") && (
          <div className="field-err" id="signup-email-err">
            {shownError("email")}
          </div>
        )}

        <label className="field">
          <span className="sr-only">닉네임</span>
          <input
            ref={nicknameRef}
            type="text"
            placeholder="닉네임 (2~30자)"
            value={nickname}
            onChange={(e) => {
              setNickname(e.target.value);
              clearServer("nickname");
            }}
            onBlur={() => markTouched("nickname")}
            autoComplete="nickname"
            maxLength={30}
            aria-invalid={Boolean(shownError("nickname"))}
            aria-describedby={shownError("nickname") ? "signup-nickname-err" : undefined}
          />
          <AvailNote state={nickAvail} />
        </label>
        {shownError("nickname") && (
          <div className="field-err" id="signup-nickname-err">
            {shownError("nickname")}
          </div>
        )}

        <label className="field">
          <span className="sr-only">비밀번호</span>
          <input
            ref={passwordRef}
            type={showPw ? "text" : "password"}
            placeholder="비밀번호"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              clearServer("password");
            }}
            onBlur={() => markTouched("password")}
            autoComplete="new-password"
            aria-invalid={Boolean(shownError("password"))}
            aria-describedby="signup-pw-rules"
          />
          <EyeToggle shown={showPw} onToggle={() => setShowPw((v) => !v)} />
        </label>
        <ul className="pw-rules" id="signup-pw-rules">
          <li className={rules.len ? "ok" : ""}>8자 이상</li>
          <li className={rules.letter ? "ok" : ""}>영문 포함</li>
          <li className={rules.digit ? "ok" : ""}>숫자 포함</li>
          {rules.tooLong && <li className="bad">64자 이하</li>}
        </ul>
        {shownError("password") && <div className="field-err">{shownError("password")}</div>}

        <label className="field">
          <span className="sr-only">비밀번호 확인</span>
          <input
            ref={confirmRef}
            type={showPw ? "text" : "password"}
            placeholder="비밀번호 확인"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            onBlur={() => markTouched("passwordConfirm")}
            autoComplete="new-password"
            aria-invalid={Boolean(shownError("passwordConfirm"))}
            aria-describedby={shownError("passwordConfirm") ? "signup-confirm-err" : undefined}
          />
          {Boolean(password) && confirm === password && <span className="avail yes">일치</span>}
          <EyeToggle shown={showPw} onToggle={() => setShowPw((v) => !v)} />
        </label>
        {shownError("passwordConfirm") && (
          <div className="field-err" id="signup-confirm-err">
            {shownError("passwordConfirm")}
          </div>
        )}

        {formErr && <div className="form-err">{formErr}</div>}
        <button type="submit" className="btn btn-p" disabled={busy || emailAvail === "no" || nickAvail === "no"} style={{ padding: 12 }}>
          {busy ? "가입 중…" : "가입하고 시작하기"}
        </button>
        <div className="hint" style={{ textAlign: "center" }}>
          이미 계정이 있나요?{" "}
          <button type="button" className="link-btn" onClick={() => openAuth("login")}>
            로그인
          </button>
        </div>
      </div>
    </form>
  );
}
