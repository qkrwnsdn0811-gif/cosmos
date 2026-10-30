-- Google 로그인: Google 계정 고유 ID(sub)를 회원에 연결한다.
-- Google 로만 가입한 회원은 비밀번호가 없으므로 password 를 비워 둘 수 있게 한다.
ALTER TABLE users ADD COLUMN google_sub VARCHAR(255);
ALTER TABLE users ALTER COLUMN password DROP NOT NULL;

CREATE UNIQUE INDEX ux_users_google_sub
    ON users (google_sub)
    WHERE deleted_at IS NULL AND google_sub IS NOT NULL;
