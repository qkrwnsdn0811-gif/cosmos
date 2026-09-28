import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError } from "@/api";
import { useUpdateNickname } from "@/lib/queries";
import { useSession } from "@/store/session";
import { useUi } from "@/store/ui";
import { Skeleton } from "@/components/ui";
import "./profile.css";

/**
 * 내 페이지 — 프로필 메뉴 진입점. 내 정보만 다룬다.
 * 관심 기업·스크랩은 별도 화면(/me/watchlist, MyItemsPage)으로 분리했다 — 지금은 분리만, 레이아웃 재설계는 다음 단계.
 * 뉴스·공시 비율은 서버에 저장하지 않는 화면 설정이라 여기가 아니라 은하 하단 조절 도크(ControlDock)의 고급 영역에 있다.
 */
export default function MePage() {
  const status = useSession((s) => s.status);
  const user = useSession((s) => s.user);
  const openAuth = useUi((s) => s.openAuth);
  const navigate = useNavigate();

  useEffect(() => {
    if (status === "guest") {
      openAuth("login");
      navigate("/", { replace: true });
    }
  }, [status, openAuth, navigate]);

  if (status !== "authed" || !user) return <div className="page">{status === "booting" && <Skeleton h={200} />}</div>;

  return (
    <div className="page me">
      <div className="page-head">
        <div>
          <span className="pill">my page</span>
          <h1>{user.nickname}</h1>
          <p>{user.email}</p>
        </div>
      </div>
      <div className="me-grid">
        <ProfileCard />
      </div>
    </div>
  );
}

function ProfileCard() {
  const user = useSession((s) => s.user)!;
  const [nick, setNick] = useState(user.nickname);
  const update = useUpdateNickname();
  const toast = useUi((s) => s.toast);
  const err = update.error instanceof ApiError ? update.error.message : null;
  return (
    <section className="card me-card" id="profile">
      <div className="kick">profile</div>
      <h2 className="sect" style={{ fontSize: 20, marginTop: 6 }}>
        내 정보
      </h2>
      <div className="lab mt-16">이메일</div>
      <div className="mt-8">{user.email}</div>
      <div className="lab mt-16">닉네임</div>
      <div className="row mt-8" style={{ gap: 8 }}>
        <label className="field grow">
          <input value={nick} onChange={(e) => setNick(e.target.value)} maxLength={30} aria-label="닉네임" />
        </label>
        <button type="button" className="btn btn-p" disabled={nick.trim().length < 2 || nick === user.nickname || update.isPending} onClick={() => update.mutate(nick.trim(), { onSuccess: () => toast("닉네임을 변경했습니다.", "success") })}>
          저장
        </button>
      </div>
      {err && <div className="field-err mt-8">{err}</div>}
      <div className="hint mt-12">닉네임은 2~30자이며 다른 사용자와 중복될 수 없습니다. 커뮤니티 댓글에 표시됩니다.</div>
    </section>
  );
}
