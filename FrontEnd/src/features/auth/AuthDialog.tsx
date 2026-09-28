import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ApiError, api } from "@/api";
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
      </div>
    </form>
  );
}

/* ------------------------------ 회원가입 ------------------------------ */
type SignupField = "email" | "code" | "nickname" | "password" | "passwordConfirm";
const SIGNUP_ORDER: readonly SignupField[] = ["email", "code", "nickname", "password", "passwordConfirm"];

/* ------------------------------ 이메일 인증 ------------------------------ */
/**
 * 가입 전 이메일 인증 (백엔드 EmailVerificationService) — 순서는 코드 발송 → 코드 확인 → 가입.
 * 서버 정책: 코드는 5분 유효, 같은 이메일 재발송은 60초에 한 번(429), 5회 틀리면 코드 폐기, 인증 완료 상태는 30분 유지.
 * 재발송 대기 시간은 응답에 없어 명세가 권하는 60초를 프론트 타이머로 둔다.
 */
const CODE_LENGTH = 6;
const RESEND_COOLDOWN_MS = 60_000;
/**
 * 코드를 틀릴 수 있는 횟수 — 서버 기본값(app.email-verification.max-attempts). 서버는 이 횟수째 오류에서 코드를 폐기하지만 응답은
 * 여전히 EMAIL_CODE_INVALID 라(다음 요청부터 EXPIRED), 프론트가 세어 두지 않으면 이미 죽은 코드에 "다시 입력" 을 권하게 된다
 */
const MAX_ATTEMPTS = 5;
/** 429 로 거절돼 발송 응답을 못 받았을 때 가정하는 코드 유효 시간 — 서버 기본값(app.email-verification.code-ttl 5분) */
const CODE_TTL_FALLBACK_MS = 5 * 60_000;
/**
 * 인증 완료 표시를 탭 세션에 남긴다. 서버는 30분 동안 인증을 기억하는데, 모달을 실수로 닫았다가 다시 열 때마다 코드를 새로 받게
 * 하면(60초 대기까지) 번거롭다. 최종 판정은 서버가 하므로(가입 403 EMAIL_NOT_VERIFIED → reset) 오래된 값이 남아도 안전하다
 */
const VERIFIED_KEY = "cosmos.signup.verified";
interface VerifiedMark {
  /** 소문자로 정규화한 이메일 (서버 응답값) */
  email: string;
  until: number;
}
function readVerified(): VerifiedMark | null {
  try {
    const raw = sessionStorage.getItem(VERIFIED_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<VerifiedMark>;
    return typeof v.email === "string" && typeof v.until === "number" && v.until > Date.now() ? { email: v.email, until: v.until } : null;
  } catch {
    return null;
  }
}
function writeVerified(v: VerifiedMark | null) {
  try {
    if (v) sessionStorage.setItem(VERIFIED_KEY, JSON.stringify(v));
    else sessionStorage.removeItem(VERIFIED_KEY);
  } catch {
    /* 저장 실패는 가입 흐름을 막지 않는다 */
  }
}
const normalizeEmail = (v: string) => v.trim().toLowerCase();
function mmss(ms: number) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

interface SentCode {
  email: string;
  expiresAt: number;
  resendAt: number;
}
/**
 * 발송·확인이 실패했을 때 폼이 이메일 칸 또는 폼 전체에 보여 줄 오류. 코드 칸 오류는 훅이 직접 들고 있다.
 * target 은 요청이 나간 이메일 — 응답을 기다리는 동안 이메일을 고쳤으면 그 판정은 지금 값의 것이 아니므로 폼이 걸러 낸다
 */
type VerifyFailure = { field: "email"; message: string; taken?: boolean; target: string } | { field: "form"; message: string } | null;

/**
 * 이메일 인증 상태 머신. 판정은 모두 "지금 입력된 이메일" 기준이라, 이메일을 한 글자라도 고치면 인증·발송 상태가 자동으로
 * 비활성이 된다(다른 주소의 인증이 살아남지 않는다). 원래 주소로 되돌리면 그대로 되살아난다.
 */
function useEmailVerification(email: string, emailValid: boolean) {
  const normalized = normalizeEmail(email);
  const [verified, setVerified] = useState<VerifiedMark | null>(readVerified);
  const [sent, setSent] = useState<SentCode | null>(null);
  const [code, setCode] = useState("");
  const [codeErr, setCodeErr] = useState<string | null>(null);
  /** 발송 요청이 나가 있는 이메일(정규화) — 불리언으로 두면 요청 중에 이메일을 고쳤을 때 다른 주소에 "보내는 중" 이 붙는다 */
  const [sendingFor, setSendingFor] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [wrongAttempts, setWrongAttempts] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  /** 발송이든 확인이든 하나라도 진행 중 — 둘이 동시에 나가면 새 코드 발송이 확인 결과를 덮어쓴다 */
  const inFlight = sendingFor !== null || verifying;

  const isVerified = verified !== null && verified.email === normalized && verified.until > now;
  // 코드가 만료돼도 행은 남겨 "다시 받기" 를 보여 준다
  const isSent = !isVerified && sent !== null && sent.email === normalized;
  const codeExpired = isSent && sent.expiresAt <= now;
  const resendIn = isSent ? Math.max(0, sent.resendAt - now) : 0;

  // 남은 시간 표시용 초 단위 틱 — 보여 줄 타이머가 있을 때만 돈다
  useEffect(() => {
    if (!sent && !verified) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [sent, verified]);

  const mark = (v: VerifiedMark | null) => {
    setVerified(v);
    writeVerified(v);
  };

  const send = async (): Promise<VerifyFailure> => {
    if (inFlight || !emailValid) return null;
    const target = normalized;
    setSendingFor(target);
    setCodeErr(null);
    try {
      const res = await api.auth.sendEmailCode({ email: email.trim() });
      const t = Date.now();
      setSent({ email: normalizeEmail(res.email), expiresAt: t + res.expiresInSeconds * 1000, resendAt: t + RESEND_COOLDOWN_MS });
      setCode("");
      setWrongAttempts(0);
      setNow(t);
      return null;
    } catch (x) {
      if (x instanceof ApiError) {
        if (x.code === "EMAIL_DUPLICATED") return { field: "email", message: x.message, taken: true, target };
        if (x.code === "EMAIL_CODE_TOO_FREQUENT") {
          // 60초 안의 재요청. 앞서 보낸 코드는 아직 살아 있으므로 입력 행을 열어 두고 재발송 타이머만 다시 건다.
          // 모달을 닫았다 다시 열어 발송 기록이 없으면 유효 시간을 추정해야 하는데, 429 는 "60초 안에 발송했다" 는 뜻이므로
          // 가장 이른 발송 시점(60초 전)을 기준으로 잡는다 — 실제보다 짧게 볼 수는 있어도, 이미 만료된 코드를 유효하다고 보여 주지는 않는다
          const t = Date.now();
          setSent((s) => (s && s.email === target ? { ...s, resendAt: t + RESEND_COOLDOWN_MS } : { email: target, expiresAt: t + CODE_TTL_FALLBACK_MS - RESEND_COOLDOWN_MS, resendAt: t + RESEND_COOLDOWN_MS }));
          setCodeErr(x.message);
          return null;
        }
        const fields = pickFieldErrors(x, ["email"]);
        if (fields.email) return { field: "email", message: fields.email, target };
        // 500 INTERNAL_SERVER_ERROR = 메일 발송 실패 (코드도 저장되지 않았다)
        return { field: "form", message: x.status >= 500 ? "인증 메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요." : x.message };
      }
      return { field: "form", message: GENERIC_ERROR };
    } finally {
      setSendingFor(null);
    }
  };

  /** 코드가 죽었을 때(만료·폐기) — 입력 행은 남기고 재발송을 대기 없이 열어 준다 */
  const expireCode = (message: string) => {
    setSent((s) => (s ? { ...s, expiresAt: 0, resendAt: 0 } : s));
    setCode("");
    setWrongAttempts(0);
    setCodeErr(message);
  };

  const verify = async (): Promise<VerifyFailure> => {
    if (inFlight || !isSent || code.length !== CODE_LENGTH) return null;
    const target = normalized;
    setVerifying(true);
    setCodeErr(null);
    try {
      const res = await api.auth.verifyEmailCode({ email: email.trim(), code });
      const t = Date.now();
      mark({ email: normalizeEmail(res.email), until: t + res.validForSeconds * 1000 });
      setSent(null);
      setCode("");
      setWrongAttempts(0);
      setNow(t);
      return null;
    } catch (x) {
      if (x instanceof ApiError) {
        // 명세: EMAIL_CODE_INVALID 는 "다시 입력", EMAIL_CODE_EXPIRED 는 "재발송" 안내로 구분한다
        if (x.code === "EMAIL_CODE_INVALID") {
          const next = wrongAttempts + 1;
          setWrongAttempts(next);
          // 서버는 이 횟수째 오류에서 코드를 폐기하고도 INVALID 를 돌려준다 — 여기서 만료로 다뤄 헛된 6번째 시도를 막는다
          if (next >= MAX_ATTEMPTS) expireCode(`인증 코드를 ${MAX_ATTEMPTS}회 틀려 폐기되었습니다. 다시 받아 주세요.`);
          else setCodeErr(`${x.message} (남은 시도 ${MAX_ATTEMPTS - next}회)`);
          return null;
        }
        if (x.code === "EMAIL_CODE_EXPIRED") {
          // 코드가 없거나 만료·폐기됨 → 기다리지 않고 바로 다시 받을 수 있게 한다
          expireCode(x.message);
          return null;
        }
        const fields = pickFieldErrors(x, ["email", "code"]);
        if (fields.code) {
          setCodeErr(fields.code);
          return null;
        }
        if (fields.email) return { field: "email", message: fields.email, target };
        return { field: "form", message: x.message };
      }
      return { field: "form", message: GENERIC_ERROR };
    } finally {
      setVerifying(false);
    }
  };

  /** 서버 쪽 인증 상태가 사라졌을 때(가입 403 EMAIL_NOT_VERIFIED) 또는 가입을 마쳤을 때 — 처음 상태로 */
  const reset = () => {
    mark(null);
    setSent(null);
    setCode("");
    setWrongAttempts(0);
    setCodeErr(null);
  };
  const changeCode = (v: string) => {
    setCode(v.replace(/\D/g, "").slice(0, CODE_LENGTH));
    if (codeErr) setCodeErr(null);
  };

  return {
    isVerified,
    isSent,
    codeExpired,
    resendIn,
    codeLeft: isSent ? Math.max(0, sent.expiresAt - now) : 0,
    code,
    codeErr,
    /** 지금 입력된 이메일로 발송 요청이 나가 있다 (버튼 문구용) */
    sending: sendingFor === normalized,
    verifying,
    inFlight,
    send,
    verify,
    reset,
    changeCode,
  };
}

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
  const codeRef = useRef<HTMLInputElement>(null);
  const nicknameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);
  /** 제출 실패 시 첫 번째 문제 필드로 보낸다 */
  const focusField = (f: SignupField) => {
    const el =
      f === "email" ? emailRef.current : f === "code" ? codeRef.current : f === "nickname" ? nicknameRef.current : f === "password" ? passwordRef.current : confirmRef.current;
    el?.focus();
    el?.scrollIntoView({ block: "nearest" });
  };

  const emailError = validateEmail(email);
  const nicknameError = validateNickname(nickname);
  const passwordError = validatePassword(password);
  const confirmError = !confirm ? "비밀번호를 한 번 더 입력해 주세요." : confirm !== password ? "비밀번호가 일치하지 않습니다." : null;
  const clientError: Record<SignupField, string | null> = {
    email: emailError,
    code: null, // 코드는 서버가 판정한다 — 인증 여부는 제출 시 verification.isVerified 로 본다
    nickname: nicknameError,
    password: passwordError,
    passwordConfirm: confirmError,
  };
  /** 서버가 돌려준 오류가 먼저, 그다음 blur/제출로 드러난 클라이언트 오류 */
  const shownError = (f: SignupField) => serverErr[f] ?? (touched[f] ? clientError[f] : null);

  const [emailAvail, markEmailTaken] = useAvailability("email", email.trim(), emailError === null);
  const [nickAvail, markNickTaken] = useAvailability("nickname", nickname.trim(), nicknameError === null);
  const verification = useEmailVerification(email, emailError === null);

  const markTouched = (f: SignupField) => setTouched((t) => ({ ...t, [f]: true }));
  const clearServer = (f: SignupField) => setServerErr((s) => (s[f] ? { ...s, [f]: undefined } : s));

  // 코드 입력 행은 발송 성공 뒤에야 그려지므로, 그 렌더가 끝난 다음 한 번만 포커스를 옮긴다
  const focusCodeNext = useRef(false);
  useEffect(() => {
    if (focusCodeNext.current && codeRef.current) {
      focusCodeNext.current = false;
      codeRef.current.focus();
    }
  });

  /** 응답을 기다리는 동안 이메일을 고쳤는지 — 입력 DOM 값이 가장 최신이다 (이 함수들은 클릭 시점 렌더의 클로저라 state 는 오래됐을 수 있다) */
  const emailStill = (target: string) => normalizeEmail(emailRef.current?.value ?? "") === target;
  const applyFailure = (f: VerifyFailure) => {
    if (!f) return;
    if (f.field === "email") {
      // 중복 판정은 요청한 값에 대한 것이라 그 값의 캐시에 남긴다 — 그 주소로 되돌려도 "사용 가능" 이 되살아나지 않는다
      if (f.taken) markEmailTaken();
      // 그 사이 다른 주소로 고쳤다면 지금 값에 대한 오류가 아니다 — 표시하지도, 포커스를 끌어오지도 않는다
      if (!emailStill(f.target)) return;
      setServerErr((s) => ({ ...s, email: f.message }));
      focusField("email");
    } else {
      setFormErr(f.message);
    }
  };
  const sendCode = async () => {
    setFormErr(null);
    clearServer("email");
    markTouched("email");
    if (emailError) {
      focusField("email");
      return;
    }
    const target = normalizeEmail(email);
    const f = await verification.send();
    applyFailure(f);
    if (!f && emailStill(target)) focusCodeNext.current = true;
  };
  const verifyCode = async () => {
    setFormErr(null);
    const f = await verification.verify();
    applyFailure(f);
  };

  const rules = {
    len: password.length >= 8,
    letter: /[A-Za-z]/.test(password),
    digit: /\d/.test(password),
    tooLong: password.length > 64,
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setTouched({ email: true, code: true, nickname: true, password: true, passwordConfirm: true });
    setServerErr({});
    setFormErr(null);
    if (clientError.email) {
      focusField("email");
      return;
    }
    // 서버가 403 으로 거절할 요청을 보내지 않는다 — 인증부터 끝내도록 이메일 칸에 알린다
    if (!verification.isVerified) {
      setServerErr({ email: "이메일 인증을 먼저 완료해 주세요." });
      focusField(verification.isSent ? "code" : "email");
      return;
    }
    const firstBad = SIGNUP_ORDER.find((f) => clientError[f]);
    if (firstBad) {
      focusField(firstBad);
      return;
    }
    setBusy(true);
    const nick = nickname.trim();
    try {
      const { autoLogin } = await signup(email.trim(), password, nick);
      // 가입이 끝나면 서버의 인증 완료 상태도 사라진다 — 세션에 남긴 표시를 같이 지운다
      verification.reset();
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
          // 인증 완료 상태는 서버에 그대로 남는다 — 닉네임만 바꿔 다시 제출할 수 있다
          setServerErr({ nickname: x.message });
          markNickTaken();
          focusField("nickname");
        } else if (x.code === "EMAIL_NOT_VERIFIED") {
          // 인증 완료 후 30분이 지났거나 서버 상태가 사라짐 → 코드 발송부터 다시
          verification.reset();
          setServerErr({ email: "이메일 인증이 만료되었습니다. 인증 코드를 다시 받아 주세요." });
          focusField("email");
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

  const sendLabel = verification.sending
    ? "보내는 중…"
    : !verification.isSent
      ? "인증 코드 받기"
      : verification.resendIn > 0
        ? `재발송 ${Math.ceil(verification.resendIn / 1000)}초`
        : "다시 받기";
  // 발송과 확인은 서로 잠근다 — 확인 응답을 기다리는 동안 새 코드가 나가면 방금 맞힌 코드가 무효가 된다
  const canSend = !verification.inFlight && !verification.isVerified && emailError === null && emailAvail !== "no" && verification.resendIn === 0;
  const canVerify = !verification.inFlight && verification.isSent && !verification.codeExpired && verification.code.length === CODE_LENGTH;

  return (
    <form onSubmit={submit} noValidate>
      <h2 id="auth-title">탐사대에 합류하세요</h2>
      <p className="hint">이메일 인증을 마친 뒤 닉네임과 비밀번호로 가입합니다. 비밀번호는 8~64자, 영문과 숫자를 각 1자 이상 포함합니다.</p>
      <div className="form">
        <div className="field-row">
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
            {verification.isVerified ? <span className="avail yes">인증 완료</span> : <AvailNote state={emailAvail} />}
          </label>
          {!verification.isVerified && (
            <button type="button" className="btn btn-g btn-sm" onClick={sendCode} disabled={!canSend} aria-live="polite">
              {sendLabel}
            </button>
          )}
        </div>
        {shownError("email") && (
          <div className="field-err" id="signup-email-err">
            {shownError("email")}
          </div>
        )}

        {verification.isSent && (
          <>
            <div className="field-row">
              <label className="field">
                <span className="sr-only">인증 코드</span>
                <input
                  ref={codeRef}
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  placeholder={`인증 코드 ${CODE_LENGTH}자리`}
                  value={verification.code}
                  onChange={(e) => verification.changeCode(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter 는 가입 제출이 아니라 코드 확인이다
                    if (e.key === "Enter") {
                      e.preventDefault();
                      if (canVerify) void verifyCode();
                    }
                  }}
                  autoComplete="one-time-code"
                  maxLength={CODE_LENGTH}
                  aria-invalid={Boolean(verification.codeErr)}
                  aria-describedby="signup-code-note"
                />
                <span className={verification.codeExpired ? "avail no" : "avail"}>{verification.codeExpired ? "만료" : mmss(verification.codeLeft)}</span>
              </label>
              <button type="button" className="btn btn-p btn-sm" onClick={verifyCode} disabled={!canVerify}>
                {verification.verifying ? "확인 중…" : "확인"}
              </button>
            </div>
            {verification.codeErr ? (
              <div className="field-err" id="signup-code-note">
                {verification.codeErr}
              </div>
            ) : (
              <div className="field-hint" id="signup-code-note">
                {verification.codeExpired ? "인증 코드 유효 시간이 지났습니다. 다시 받아 주세요." : `메일로 받은 ${CODE_LENGTH}자리 코드를 입력해 주세요. 메일이 없으면 스팸함도 확인해 주세요.`}
              </div>
            )}
          </>
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
