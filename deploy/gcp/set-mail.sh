#!/usr/bin/env bash
# Sets the Gmail account that sends sign-up verification codes, then redeploys the running release
# so the backend picks it up. The app password is read without echo and never printed.
set -Eeuo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
env_file=/etc/cosmos/app.env

for attempt in 1 2 3; do
    read -r -p 'Gmail 주소: ' mail_user
    [[ "$mail_user" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] && break
    echo '이메일 주소 형식이 아닙니다. 입력기가 영문인지 확인하고 다시 입력하세요.' >&2
    [[ "$attempt" -lt 3 ]] || exit 2
done
for attempt in 1 2 3; do
    read -r -s -p '앱 비밀번호 16자리 (입력은 보이지 않음, 띄어쓰기 있어도 됨): ' mail_password
    echo
    # Google shows the password in groups; copied text can carry ordinary or no-break spaces.
    mail_password="${mail_password//$'\xc2\xa0'/}"
    mail_password="${mail_password//[[:space:]]/}"
    [[ "$mail_password" =~ ^[A-Za-z]{16}$ ]] && break
    if LC_ALL=C grep -q '[^ -~]' <<< "$mail_password"; then
        echo '영문이 아닌 글자가 섞여 있습니다. 입력기를 영문으로 바꾸고 다시 붙여넣으세요.' >&2
    else
        echo "앱 비밀번호는 영문 16자입니다 (받은 글자 수: ${#mail_password}). Gmail 로그인 비밀번호가 아닌 앱 비밀번호를 붙여넣으세요." >&2
    fi
    [[ "$attempt" -lt 3 ]] || exit 2
done

temporary="$(mktemp /etc/cosmos/.app.env.XXXXXX)"
grep -v -e '^MAIL_USERNAME=' -e '^MAIL_PASSWORD=' "$env_file" > "$temporary"
printf 'MAIL_USERNAME=%s\nMAIL_PASSWORD=%s\n' "$mail_user" "$mail_password" >> "$temporary"
unset mail_password
chown root:cosmos-ci "$temporary"
chmod 0640 "$temporary"
mv "$temporary" "$env_file"
echo "메일 설정을 저장했습니다: $mail_user"

tag="$(cat /opt/cosmos/releases/current)"
public_url="$(sed -n 's/^PUBLIC_URL=//p' "$env_file")"
echo "백엔드를 다시 띄웁니다 ($tag)..."
sudo -u cosmos-ci env COSMOS_SMOKE_URL="$public_url" bash -c 'cd /opt/cosmos/checkouts/cosmos && bash deploy/scripts/deploy.sh "$0"' "$tag"
echo '완료. 회원가입 화면에서 인증 코드를 요청해 보세요.'
