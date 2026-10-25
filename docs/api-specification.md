# API 명세

이 문서는 COSMOS 서비스의 프론트엔드와 백엔드가 공유하는 REST API 계약을 정의한다. 실제 구현이 변경되면 코드와 이 문서를 함께 수정한다.

- 기본 경로: `/api`
- 응답 형식: JSON
- 필드명: camelCase
- 주요 도메인 식별자: UUID 문자열
- 사용자 식별자(`userId`): 64비트 정수
- 날짜·시각: ISO 8601 UTC

## 1. 공통 규칙

### 인증

- Access Token은 `Authorization: Bearer {accessToken}` 헤더로 전달한다.
- Refresh Token 원문은 HttpOnly·Secure 쿠키로 전달한다.
- 공개 조회 API는 비로그인 사용자도 호출할 수 있다.
- 관심 기업, 뉴스 스크랩, 댓글 작성·수정·삭제와 내 정보 API는 로그인이 필요하다.
- 회원가입은 이메일 인증을 먼저 거친다. 순서는 인증 코드 발송 → 코드 확인 → 회원가입이며, 인증 완료 상태는 30분 유지된다.
- 공개 API에 유효한 Access Token이 선택적으로 전달되면 스크랩 여부 등을 응답에 반영할 수 있다.

### 성공 응답

- 별도의 `data` 래퍼 없이 실제 응답 객체를 반환한다.
- 삭제 성공처럼 응답 본문이 필요하지 않으면 `204 No Content`를 반환한다.
- 빈 목록은 `[]`, 값이 존재하지 않는 선택 필드는 `null`을 사용한다.

### 목록과 페이지네이션

- 목록 API는 커서 기반 페이지네이션을 사용한다.
- `size`의 기본값은 20, 최대값은 100이다. API별 별도 제한이 있으면 상세 명세를 따른다.
- 잘못된 커서는 `400 INVALID_CURSOR`를 반환한다.

```json
{
  "items": [],
  "nextCursor": null,
  "hasNext": false
}
```

### 오류 응답

```json
{
  "code": "COMPANY_NOT_FOUND",
  "message": "기업을 찾을 수 없습니다.",
  "fieldErrors": []
}
```

입력값 검증에 실패하면 `fieldErrors`에 문제가 발생한 필드와 사유를 반환한다.

```json
{
  "code": "VALIDATION_FAILED",
  "message": "요청값이 올바르지 않습니다.",
  "fieldErrors": [
    {
      "field": "email",
      "message": "올바른 형식의 이메일 주소여야 합니다."
    }
  ]
}
```

| 상태 | 용도 |
| --- | --- |
| `400 Bad Request` | 입력값 또는 커서 검증 실패 |
| `401 Unauthorized` | 인증 실패 또는 토큰 만료 |
| `403 Forbidden` | 리소스 접근 권한 부족 |
| `404 Not Found` | 요청한 리소스 없음 |
| `405 Method Not Allowed` | 허용되지 않은 HTTP 메서드 |
| `409 Conflict` | 이메일·닉네임·스크랩 등 중복 등록 |
| `415 Unsupported Media Type` | 지원하지 않는 요청 미디어 타입 |
| `500 Internal Server Error` | 서버 내부 오류 |

### 서비스 범위

- 관리자 화면과 관리자 API는 MVP 범위에서 제외한다.
- 공시는 관계 분석에 사용하지만 서비스 화면에 문서 자체를 공개하지 않는다.
- 스크랩은 뉴스만 지원한다.
- 전체 관계 그래프의 노드 수에는 고정 상한을 두지 않는다.
- 기업 중심 그래프는 최대 3단계까지 탐색한다.
- 그래프 API는 `PUBLISHED` 상태의 최신 스냅샷을 사용한다.
- 그래프 조회 요청은 `REPEATABLE READ` 트랜잭션으로 스냅샷과 점수의 시점을 일치시킨다. RDB Loader가 새 결과를 게시하는 동안에도 같은 응답에 이전 스냅샷 ID와 새 점수가 섞이지 않는다.
- 사용자 뉴스·공시 가중치는 서버에 저장하지 않는다. 프론트엔드는 브라우저 탭의 `sessionStorage`에서만 유지한다.

## 2. API 목록

### auth | 인증

| 메서드 | URI | API |
| --- | --- | --- |
| `GET` | `/api/auth/check-email` | 이메일 중복 확인 |
| `GET` | `/api/auth/check-nickname` | 닉네임 중복 확인 |
| `POST` | `/api/auth/email/send-code` | 이메일 인증 코드 발송 |
| `POST` | `/api/auth/email/verify-code` | 이메일 인증 코드 확인 |
| `POST` | `/api/auth/login` | 로그인 |
| `POST` | `/api/auth/logout` | 로그아웃 |
| `POST` | `/api/auth/refresh` | refresh토큰 재발급 |
| `POST` | `/api/auth/signup` | 회원가입 |

### users | 사용자

| 메서드 | URI | API |
| --- | --- | --- |
| `GET` | `/api/users/me` | 내 정보 조회 |
| `PATCH` | `/api/users/me` | 내 닉네임 수정 |
| `GET` | `/api/users/me/scraps` | 내 뉴스 스크랩 목록 조회 |
| `DELETE` | `/api/users/me/scraps/{newsId}` | 뉴스 스크랩 해제 |
| `POST` | `/api/users/me/scraps/{newsId}` | 뉴스 스크랩 등록 |
| `GET` | `/api/users/me/watch-companies` | 내 관심 기업 목록 조회 |
| `DELETE` | `/api/users/me/watch-companies/{companyId}` | 관심 기업 해제 |
| `POST` | `/api/users/me/watch-companies/{companyId}` | 관심 기업 등록 |

### companies | 기업·산업

| 메서드 | URI | API |
| --- | --- | --- |
| `GET` | `/api/companies` | 기업 목록·검색 |
| `GET` | `/api/companies/{companyId}` | 기업 상세 조회 |
| `GET` | `/api/companies/{companyId}/metrics` | 기업 최신 지표 조회 |
| `GET` | `/api/companies/{companyId}/metrics/history` | 기업 지표 이력 조회 |
| `GET` | `/api/companies/{companyId}/stock-prices` | 기업 주가 이력 조회 |
| `GET` | `/api/industries` | 산업 목록 조회 |

### news | 뉴스

| 메서드 | URI | API |
| --- | --- | --- |
| `GET` | `/api/news` | 뉴스 목록·검색 |
| `GET` | `/api/news/{newsId}` | 뉴스 상세 조회 |

### graph | 관계 지도

| 메서드 | URI | API |
| --- | --- | --- |
| `GET` | `/api/graphs/companies/{companyId}` | 기업 중심 관계 그래프 조회 |
| `GET` | `/api/graphs/latest` | 최신 전체 관계 그래프 조회 |
| `GET` | `/api/relationships/{relationshipId}` | 기업 관계 상세 조회 |
| `GET` | `/api/relationships/{relationshipId}/evidence` | 기업 관계 뉴스 근거 조회 |

### events | NEXUS 이벤트

| 메서드 | URI | API |
| --- | --- | --- |
| `GET` | `/api/events` | 이벤트 목록 |
| `GET` | `/api/events/{eventId}` | 이벤트 관계 지도 조회 |
| `GET` | `/api/events/{eventId}/companies/{companyId}/chart` | 뉴스 전후 주가 차트 |
| `GET` | `/api/events/backtest` | 방향 딱지 적중률 |
| `GET` | `/api/events/company-history` | 기업 과거 뉴스와 주가 반응 |
| `GET` | `/api/companies/{companyId}/fundamental` | 기업 지표 (NEXUS 투자자 화면용, 경로는 companies) |

### market | NEXUS 기업 탭 시세

| 메서드 | URI | API |
| --- | --- | --- |
| `GET` | `/api/market/indices` | 코스피·코스닥 지수 |
| `GET` | `/api/companies/quotes` | 전 종목 시세 (경로는 companies) |
| `GET` | `/api/companies/{companyId}/related` | 관계 기업 시세 (경로는 companies) |

### community | 커뮤니티

| 메서드 | URI | API |
| --- | --- | --- |
| `DELETE` | `/api/comments/{commentId}` | 기업 커뮤니티 댓글 삭제 |
| `PATCH` | `/api/comments/{commentId}` | 기업 커뮤니티 댓글 수정 |
| `GET` | `/api/community/comments` | 전체 최신 커뮤니티 댓글 조회 |
| `GET` | `/api/companies/{companyId}/comments` | 기업별 커뮤니티 댓글 조회 |
| `POST` | `/api/companies/{companyId}/comments` | 기업 커뮤니티 댓글 작성 |

## 3. API 상세

### auth | 인증

#### GET /api/auth/check-email — 이메일 중복 확인

#### Request
바디 없음 — 쿼리 파라미터로 전달<br>`GET /api/auth/check-email?email=kim@example.com`
#### Response — 200 OK
```json
{
  "available": true
}
```
사용 가능하면 true, 이미 사용 중이면 false. 대소문자는 무시하고 판단
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>이메일 형식 오류</td>
</tr>
</table>

#### GET /api/auth/check-nickname — 닉네임 중복 확인

#### Request
바디 없음 — 쿼리 파라미터로 전달<br>`GET /api/auth/check-nickname?nickname=우주탐험가`
#### Response — 200 OK
```json
{
  "available": true
}
```
사용 가능하면 true, 이미 사용 중이면 false
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>닉네임 길이 또는 형식 오류</td>
</tr>
</table>

#### POST /api/auth/email/send-code — 이메일 인증 코드 발송

#### 목적
가입 전 이메일 소유 확인. 숫자 6자리 인증 코드를 메일로 보낸다. 가입 흐름은 코드 발송 → 코드 확인 → 회원가입 순서다.
#### 인증
불필요
#### Request
```json
{
  "email": "kim@example.com"
}
```
<table header-row="true">
<tr>
<td>필드</td>
<td>규칙</td>
</tr>
<tr>
<td>email</td>
<td>이메일 형식, 255자 이하. 소문자로 정규화해 처리</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "email": "kim@example.com",
  "expiresInSeconds": 300
}
```
#### Notes
- 코드는 5분 동안 유효하다. 같은 이메일로 다시 보내면 이전 코드는 무효가 된다.
- 같은 이메일에는 60초에 한 번만 보낼 수 있다 (재발송 버튼은 60초 타이머 권장).
- 메일 발송이 실패하면 코드도 저장하지 않는다.
- 발신은 팀 공용 Gmail, 제목은 `[COSMOS] 이메일 인증 코드`.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>이메일 형식 오류 (fieldErrors 포함)</td>
</tr>
<tr>
<td>409</td>
<td>EMAIL_DUPLICATED</td>
<td>이미 가입된 이메일</td>
</tr>
<tr>
<td>429</td>
<td>EMAIL_CODE_TOO_FREQUENT</td>
<td>60초 안에 같은 이메일로 다시 요청</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>메일 발송 실패</td>
</tr>
</table>

#### POST /api/auth/email/verify-code — 이메일 인증 코드 확인

#### 목적
메일로 받은 코드를 확인해 "인증 완료" 상태를 만든다. 이 상태가 있어야 회원가입이 가능하다.
#### 인증
불필요
#### Request
```json
{
  "email": "kim@example.com",
  "code": "482913"
}
```
<table header-row="true">
<tr>
<td>필드</td>
<td>규칙</td>
</tr>
<tr>
<td>email</td>
<td>코드를 받은 이메일. 소문자로 정규화해 처리</td>
</tr>
<tr>
<td>code</td>
<td>숫자 6자리</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "email": "kim@example.com",
  "verified": true,
  "validForSeconds": 1800
}
```
#### Notes
- 인증 완료 상태는 30분 유지된다. 이 안에 회원가입을 마쳐야 하며, 가입이 끝나면 상태는 사라진다.
- 코드는 한 번 맞으면 폐기된다 (재사용 불가).
- 5회 틀리면 코드가 폐기되어 다시 발송받아야 한다.
- 프론트는 EMAIL_CODE_EXPIRED는 "재발송" 안내, EMAIL_CODE_INVALID는 "다시 입력" 안내로 구분한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>이메일 형식 오류 또는 코드가 숫자 6자리가 아님 (fieldErrors 포함)</td>
</tr>
<tr>
<td>400</td>
<td>EMAIL_CODE_INVALID</td>
<td>코드 불일치 (다시 입력 가능)</td>
</tr>
<tr>
<td>400</td>
<td>EMAIL_CODE_EXPIRED</td>
<td>발송된 코드가 없거나 만료·폐기됨 (다시 발송 필요)</td>
</tr>
</table>

#### POST /api/auth/login — 로그인

#### Request
```json
{
  "email": "kim@example.com",
  "password": "password1"
}
```
#### Response — 200 OK
```json
{
  "accessToken": "eyJhbGciOi..."
}
```
refresh token은 httpOnly 쿠키(`Set-Cookie`)로 전달
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>입력 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>INVALID_CREDENTIALS</td>
<td>이메일 없음 또는 비밀번호 불일치 (구분 안 함)</td>
</tr>
</table>

#### POST /api/auth/logout — 로그아웃

#### Request
바디 없음 — `Authorization: Bearer {accessToken}` 헤더 필요
#### Response — 204 No Content
본문 없음. 서버가 refresh 폐기 + 쿠키 만료, 프론트는 access 폐기
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>access 만료</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_INVALID</td>
<td>토큰 위조·형식 오류</td>
</tr>
</table>

#### POST /api/auth/refresh — refresh토큰 재발급

#### Request
바디 없음 — `refresh_token` 쿠키 자동 첨부 (프론트는 `withCredentials: true` 필요)
#### Response — 200 OK
```json
{
  "accessToken": "eyJhbGciOi..."
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>refresh 만료·무효 → 로그인 페이지로</td>
</tr>
</table>

#### POST /api/auth/signup — 회원가입

#### Request
```json
{
  "email": "kim@example.com",
  "password": "password1",
  "nickname": "우주탐험가"
}
```
<table header-row="true">
<tr>
<td>필드</td>
<td>규칙</td>
</tr>
<tr>
<td>email</td>
<td>이메일 형식, 255자 이하</td>
</tr>
<tr>
<td>password</td>
<td>8~64자, 영문·숫자 각 1자 이상</td>
</tr>
<tr>
<td>nickname</td>
<td>2~30자</td>
</tr>
</table>
#### Response — 201 Created
```json
{
  "userId": 1,
  "email": "kim@example.com",
  "nickname": "우주탐험가"
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>입력 규칙 위반 (fieldErrors 포함)</td>
</tr>
<tr>
<td>409</td>
<td>EMAIL_DUPLICATED</td>
<td>이메일 중복</td>
</tr>
<tr>
<td>409</td>
<td>NICKNAME_DUPLICATED</td>
<td>닉네임 중복</td>
</tr>
<tr>
<td>403</td>
<td>EMAIL_NOT_VERIFIED</td>
<td>이메일 인증을 마치지 않았거나 인증 완료 후 30분이 지남 (코드 발송·확인부터 다시)</td>
</tr>
</table>
#### Notes
- 가입 전에 `POST /api/auth/email/send-code` → `POST /api/auth/email/verify-code`로 이메일 인증을 마쳐야 한다.
- 가입이 성공하면 인증 완료 상태는 사라지고, `users.email_verified_at`에 인증 시각이 기록된다.

### users | 사용자

#### GET /api/users/me — 내 정보 조회

#### Request
바디 없음 — `Authorization: Bearer {accessToken}` 헤더 필요
#### Response — 200 OK
```json
{
  "userId": 1,
  "email": "kim@example.com",
  "nickname": "우주탐험가"
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>access 만료 → 재발급 후 재시도</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_INVALID</td>
<td>토큰 위조·형식 오류</td>
</tr>
</table>

#### PATCH /api/users/me — 내 닉네임 수정

#### 목적
로그인 사용자가 자신의 닉네임을 수정한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Request
```json
{
  "nickname": "새로운닉네임"
}
```
<table header-row="true">
<tr>
<td>필드</td>
<td>규칙</td>
</tr>
<tr>
<td>nickname</td>
<td>2~30자, 서비스의 닉네임 중복 정책 적용</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "userId": 1,
  "email": "kim@example.com",
  "nickname": "새로운닉네임"
}
```
#### Notes
`GET /api/users/me`와 동일한 사용자 정보 응답 형식을 유지한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>닉네임 입력 규칙 위반</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>409</td>
<td>NICKNAME_DUPLICATED</td>
<td>이미 사용 중인 닉네임</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>닉네임 수정 중 서버 오류</td>
</tr>
</table>

#### GET /api/users/me/scraps — 내 뉴스 스크랩 목록 조회

#### 목적
로그인 사용자가 스크랩한 뉴스를 등록 최신순으로 조회한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Query Params
- `cursor`: scrappedAt + newsId 기반 다음 페이지 커서
- `size`: 기본 20, 최대 100
#### Response — 200 OK
```json
{
  "items": [
    {
      "newsId": "550e8400-e29b-41d4-a716-446655440020",
      "title": "반도체 공급망 협력 확대",
      "summary": "기사 요약",
      "publisher": "예시경제",
      "originalUrl": "https://example.com/news/1",
      "publishedAt": "2026-09-07T02:00:00Z",
      "scrappedAt": "2026-09-07T04:00:00Z"
    }
  ],
  "nextCursor": null,
  "hasNext": false
}
```
#### Notes
공시는 서비스에 공개하지 않으므로 스크랩 대상에 포함하지 않는다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>INVALID_CURSOR</td>
<td>커서 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>스크랩 목록 조회 중 서버 오류</td>
</tr>
</table>

#### DELETE /api/users/me/scraps/{newsId} — 뉴스 스크랩 해제

#### 목적
로그인 사용자의 스크랩에서 뉴스를 제거한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Path Variable
- `newsId`: 스크랩을 해제할 뉴스 UUID
#### Response — 204 No Content
본문 없음
#### Notes
이미 해제된 경우에도 204를 반환하는 멱등 API다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>newsId 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>뉴스 스크랩 해제 중 서버 오류</td>
</tr>
</table>

#### POST /api/users/me/scraps/{newsId} — 뉴스 스크랩 등록

#### 목적
로그인 사용자의 스크랩에 뉴스 하나를 추가한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Path Variable
- `newsId`: 스크랩할 뉴스 UUID
#### Request
바디 없음
#### Response — 201 Created
```json
{
  "newsId": "550e8400-e29b-41d4-a716-446655440020",
  "scrappedAt": "2026-09-07T04:00:00Z"
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>newsId 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>404</td>
<td>NEWS_NOT_FOUND</td>
<td>뉴스가 없거나 서비스 노출 대상이 아님</td>
</tr>
<tr>
<td>409</td>
<td>NEWS_SCRAP_DUPLICATED</td>
<td>이미 스크랩한 뉴스</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>뉴스 스크랩 등록 중 서버 오류</td>
</tr>
</table>

#### GET /api/users/me/watch-companies — 내 관심 기업 목록 조회

#### 목적
로그인 사용자가 등록한 관심 기업을 등록 최신순으로 조회한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Query Params
- `cursor`: createdAt + companyId 기반 다음 페이지 커서
- `size`: 기본 20, 최대 100
#### Response — 200 OK
```json
{
  "items": [
    {
      "companyId": "550e8400-e29b-41d4-a716-446655440010",
      "name": "삼성전자",
      "stockCode": "005930",
      "market": "KOSPI",
      "primaryIndustry": {
        "industryId": "550e8400-e29b-41d4-a716-446655440001",
        "name": "반도체"
      },
      "watchedAt": "2026-09-07T03:00:00Z"
    }
  ],
  "nextCursor": null,
  "hasNext": false
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>INVALID_CURSOR</td>
<td>커서 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_INVALID</td>
<td>토큰 위조·형식 오류</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>관심 기업 목록 조회 중 서버 오류</td>
</tr>
</table>

#### DELETE /api/users/me/watch-companies/{companyId} — 관심 기업 해제

#### 목적
로그인 사용자의 관심 기업에서 기업을 제거한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Path Variable
- `companyId`: 해제할 기업 UUID
#### Request
바디 없음
#### Response — 204 No Content
본문 없음
#### Notes
이미 등록이 해제된 경우에도 동일하게 204를 반환하는 멱등 API다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>companyId 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>관심 기업 해제 중 서버 오류</td>
</tr>
</table>

#### POST /api/users/me/watch-companies/{companyId} — 관심 기업 등록

#### 목적
로그인 사용자의 관심 기업에 기업 하나를 추가한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Path Variable
- `companyId`: 등록할 기업 UUID
#### Request
바디 없음
#### Response — 201 Created
```json
{
  "companyId": "550e8400-e29b-41d4-a716-446655440010",
  "watchedAt": "2026-09-07T03:00:00Z"
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>companyId 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>기업 없음</td>
</tr>
<tr>
<td>409</td>
<td>WATCH_COMPANY_DUPLICATED</td>
<td>이미 등록한 관심 기업</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>관심 기업 등록 중 서버 오류</td>
</tr>
</table>

### companies | 기업·산업

#### GET /api/companies — 기업 목록·검색

#### 목적
기업명·영문명·별칭·종목코드로 검색하거나 시장·산업별 기업 목록을 조회한다.
#### 인증
불필요. 이 API의 `watched`는 항상 `false`로 반환하며, 로그인 사용자의 실제 관심 여부는 프론트엔드가 관심 기업 목록 API의 결과와 결합하여 표시한다.
#### Request
바디 없음
```javascript
GET /api/companies?keyword=삼성&market=KOSPI&industryId={uuid}&size=20&cursor={cursor}
```
#### Query Params
<table header-row="true">
<tr>
<td>필드</td>
<td>필수</td>
<td>규칙</td>
</tr>
<tr>
<td>keyword</td>
<td>아니요</td>
<td>앞뒤 공백과 영문 대소문자를 무시한다.</td>
</tr>
<tr>
<td>market</td>
<td>아니요</td>
<td>KOSPI·KOSDAQ·NASDAQ 등 시장 코드</td>
</tr>
<tr>
<td>industryId</td>
<td>아니요</td>
<td>산업 UUID</td>
</tr>
<tr>
<td>cursor</td>
<td>아니요</td>
<td>다음 페이지 커서</td>
</tr>
<tr>
<td>size</td>
<td>아니요</td>
<td>기본 20, 최대 100</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "items": [
    {
      "companyId": "550e8400-e29b-41d4-a716-446655440010",
      "name": "삼성전자",
      "nameEn": "Samsung Electronics",
      "stockCode": "005930",
      "market": "KOSPI",
      "primaryIndustry": {
        "industryId": "550e8400-e29b-41d4-a716-446655440001",
        "name": "반도체"
      },
      "watched": false
    }
  ],
  "nextCursor": null,
  "hasNext": false
}
```
#### Notes
- `keyword`가 있으면 관련도순, 없으면 기업명순이 기본이다.
- `watched`는 로그인 여부와 관계없이 `false`로 반환한다. 프론트엔드는 `GET /api/users/me/watch-companies`의 결과와 결합하여 실제 관심 여부를 표시한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>INVALID_CURSOR</td>
<td>커서 형식 오류</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>size 또는 필터 값 오류</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>기업 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/companies/{companyId} — 기업 상세 조회

#### 목적
선택한 기업의 기본 정보와 대표 산업을 조회한다.
#### 인증
불필요. 이 API의 `watched`는 항상 `false`로 반환하며, 로그인 사용자의 실제 관심 여부는 프론트엔드가 관심 기업 목록 API의 결과와 결합하여 표시한다.
#### Path Variable
- `companyId`: 기업 UUID
#### Response — 200 OK
```json
{
  "companyId": "550e8400-e29b-41d4-a716-446655440010",
  "name": "삼성전자",
  "nameEn": "Samsung Electronics",
  "stockCode": "005930",
  "market": "KOSPI",
  "description": "전자·반도체 기업",
  "industries": [
    {
      "industryId": "550e8400-e29b-41d4-a716-446655440001",
      "name": "반도체",
      "primary": true
    }
  ],
  "watched": false
}
```
#### Notes
관련 뉴스·지표·주가·관계·댓글은 각 탭 진입 시 별도 API로 조회한다.
`watched`는 로그인 여부와 관계없이 `false`로 반환한다. 프론트엔드는 `GET /api/users/me/watch-companies`의 결과와 결합하여 실제 관심 여부를 표시한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>companyId 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>기업이 없거나 서비스 대상이 아님</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>기업 상세 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/companies/{companyId}/metrics — 기업 최신 지표 조회

#### 목적
기업 상세 화면에 표시할 최신 뉴스 분위기와 관계 상태 지표를 조회한다.
#### 인증
불필요
#### Query Params
- `window`: 30D·1Y·10Y, 기본 30D
#### Response — 200 OK
```json
{
  "companyId": "550e8400-e29b-41d4-a716-446655440010",
  "window": "30D",
  "newsMentionCount": 152,
  "positiveCount": 91,
  "negativeCount": 23,
  "sentimentScore": 0.62,
  "relationshipCount": 18,
  "measuredAt": "2026-09-07T06:00:00Z"
}
```
#### Notes
지표가 아직 없으면 각 수치를 `null`로 반환하고 `measuredAt`도 `null`로 반환한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>companyId 또는 window 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>기업 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>기업 지표 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/companies/{companyId}/metrics/history — 기업 지표 이력 조회

#### 목적
30일·1년·10년 기준 기업 지표의 시간별 변화를 차트용으로 조회한다.
#### 인증
불필요
#### Query Params
- `window`: 30D·1Y·10Y, 기본 30D
- `from`, `to`: 선택 조회 기간, YYYY-MM-DD
#### Response — 200 OK
```json
{
  "companyId": "550e8400-e29b-41d4-a716-446655440010",
  "window": "30D",
  "items": [
    {
      "measuredAt": "2026-09-06T00:00:00Z",
      "newsMentionCount": 145,
      "positiveCount": 85,
      "negativeCount": 21,
      "sentimentScore": 0.59,
      "relationshipCount": 17
    }
  ]
}
```
#### Notes
데이터가 없으면 `items: []`를 반환한다. 차트 범위 전체를 반환하므로 무한 스크롤을 사용하지 않는다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>기간 또는 window 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>기업 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>지표 이력 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/companies/{companyId}/stock-prices — 기업 주가 이력 조회

#### 목적
기업 상세 주가 탭에 필요한 OHLCV 차트 데이터를 조회한다.
#### 인증
불필요
#### Query Params
<table header-row="true">
<tr>
<td>필드</td>
<td>규칙</td>
</tr>
<tr>
<td>period</td>
<td>1M·3M·6M·1Y, 기본 1M</td>
</tr>
<tr>
<td>interval</td>
<td>현재 MVP는 1D만 지원</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "companyId": "550e8400-e29b-41d4-a716-446655440010",
  "period": "1M",
  "interval": "1D",
  "asOfAt": "2026-09-06T00:00:00Z",
  "items": [
    {
      "tradingAt": "2026-09-06T00:00:00Z",
      "openPrice": 72000,
      "highPrice": 73500,
      "lowPrice": 71500,
      "closePrice": 73000,
      "tradingVolume": 18234567
    }
  ]
}
```
#### Notes
- 주가 API 원본은 공개하지 않고 정규화된 OHLCV만 반환한다.
- 데이터가 없으면 `items: []`와 확인 가능한 `asOfAt`을 반환한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>period 또는 interval 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>기업 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>주가 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/industries — 산업 목록 조회

#### 목적
기업 검색과 그래프 필터에 사용할 산업 계층과 기업 수를 조회한다.
#### 인증
불필요
#### Request
바디 없음
#### Response — 200 OK
```json
{
  "items": [
    {
      "industryId": "550e8400-e29b-41d4-a716-446655440001",
      "parentIndustryId": null,
      "name": "반도체",
      "description": "반도체 관련 산업",
      "companyCount": 24
    }
  ]
}
```
#### Notes
- 최상위 산업의 `parentIndustryId`는 `null`이다.
- 기업이 없는 산업도 `companyCount: 0`으로 반환한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>산업 목록 조회 중 서버 오류</td>
</tr>
</table>

### news | 뉴스

#### GET /api/news — 뉴스 목록·검색

#### 목적
서비스에 노출되는 뉴스 목록을 검색·필터링하고 최신순으로 조회한다.
#### 인증
불필요. 유효한 Access Token이 선택적으로 전달되면 `scrapped`를 함께 반환한다.
#### Request
```javascript
GET /api/news?companyId={uuid}&sentiment=POSITIVE&from=2026-09-01&to=2026-09-07&size=20&cursor={cursor}
```
#### Query Params
<table header-row="true">
<tr>
<td>필드</td>
<td>필수</td>
<td>규칙</td>
</tr>
<tr>
<td>keyword</td>
<td>아니요</td>
<td>뉴스 제목 검색어</td>
</tr>
<tr>
<td>companyId</td>
<td>아니요</td>
<td>관련 기업 UUID</td>
</tr>
<tr>
<td>industryId</td>
<td>아니요</td>
<td>관련 산업 UUID</td>
</tr>
<tr>
<td>sentiment</td>
<td>아니요</td>
<td>POSITIVE·NEGATIVE·NEUTRAL</td>
</tr>
<tr>
<td>from, to</td>
<td>아니요</td>
<td>발행일 범위, YYYY-MM-DD</td>
</tr>
<tr>
<td>cursor</td>
<td>아니요</td>
<td>publishedAt + newsId 기반 커서</td>
</tr>
<tr>
<td>size</td>
<td>아니요</td>
<td>기본 20, 최대 100</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "items": [
    {
      "newsId": "550e8400-e29b-41d4-a716-446655440020",
      "title": "반도체 공급망 협력 확대",
      "summary": "기사 요약",
      "publisher": "예시경제",
      "originalUrl": "https://example.com/news/1",
      "publishedAt": "2026-09-07T02:00:00Z",
      "sentiment": "POSITIVE",
      "relatedCompanies": [
        {
          "companyId": "550e8400-e29b-41d4-a716-446655440010",
          "name": "삼성전자"
        }
      ],
      "scrapped": false
    }
  ],
  "nextCursor": null,
  "hasNext": false
}
```
#### Notes
- 기본 정렬은 최신순이다.
- 기사 본문 전체와 공시 데이터는 반환하지 않는다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>INVALID_CURSOR</td>
<td>커서 형식 오류</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>필터 또는 기간 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>companyId에 해당하는 기업 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>뉴스 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/news/{newsId} — 뉴스 상세 조회

#### 목적
뉴스 카드 선택 시 서비스용 상세 정보와 대표 분석 근거를 조회한다.
#### 인증
불필요. 유효한 Access Token이 선택적으로 전달되면 `scrapped`를 함께 반환한다.
#### Path Variable
- `newsId`: 뉴스 문서 UUID
#### Response — 200 OK
```json
{
  "newsId": "550e8400-e29b-41d4-a716-446655440020",
  "title": "반도체 공급망 협력 확대",
  "summary": "기사 요약",
  "publisher": "예시경제",
  "author": "홍길동",
  "originalUrl": "https://example.com/news/1",
  "publishedAt": "2026-09-07T02:00:00Z",
  "relatedCompanies": [
    {
      "companyId": "550e8400-e29b-41d4-a716-446655440010",
      "name": "삼성전자",
      "sentiment": "POSITIVE",
      "relevanceScore": 0.94,
      "impactScore": 0.81
    }
  ],
  "evidence": [
    {
      "sentence": "양사는 차세대 반도체 공급 협력을 확대한다.",
      "confidence": 0.91
    }
  ],
  "scrapped": false
}
```
#### Notes
HDFS의 기사 본문 전체는 제공하지 않으며 사용자는 `originalUrl`로 원문을 확인한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>newsId 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>NEWS_NOT_FOUND</td>
<td>뉴스가 없거나 서비스 노출 대상이 아님</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>뉴스 상세 조회 중 서버 오류</td>
</tr>
</table>

### graph | 관계 지도

#### 관계 점수 표시 규칙

- `score`는 시스템 기본 비율인 뉴스 50%·공시 50%로 계산한 공통 관계 점수다. 프론트엔드 슬라이더의 초기 `newsWeight`는 `0.5`다.
- `newsScore`와 `disclosureScore`는 사용자가 현재 화면에서 비율을 조절할 때 사용하는 구성 점수다.
- 두 구성 점수가 모두 있으면 `newsScore × newsWeight + disclosureScore × (1 - newsWeight)`로 표시 점수를 계산한다. 이때 `newsWeight`는 `0~1` 범위다.
- 한쪽 구성 점수만 있으면 가중치와 관계없이 존재하는 점수를 사용하고, 둘 다 없으면 해당 관계를 표시하지 않는다.
- 사용자 가중치와 계산 결과는 서버에 저장하지 않는다. 가중치는 같은 탭의 페이지 이동과 새로고침에서는 유지하고, 탭 세션 종료 후 다시 접속하면 기본 `score`를 사용한다.

#### GET /api/graphs/companies/{companyId} — 기업 중심 관계 그래프 조회

#### 목적
기업 노드 선택 또는 검색 후 해당 기업을 중심으로 간접 관계망을 조회한다.
#### 인증
불필요. 서버는 시스템 기본 가중치의 공통 점수와 뉴스·공시 구성 점수를 반환한다.
#### Path Variable
- `companyId`: 중심 기업 UUID
#### Query Params
<table header-row="true">
<tr>
<td>필드</td>
<td>기본</td>
<td>규칙</td>
</tr>
<tr>
<td>maxDepth</td>
<td>3</td>
<td>1~3</td>
</tr>
<tr>
<td>window</td>
<td>30D</td>
<td>30D·1Y·10Y 중 하나</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "snapshotId": "550e8400-e29b-41d4-a716-446655440030",
  "asOfAt": "2026-09-07T06:00:00Z",
  "window": "30D",
  "centerCompanyId": "550e8400-e29b-41d4-a716-446655440010",
  "personalized": false,
  "nodes": [
    {
      "companyId": "550e8400-e29b-41d4-a716-446655440010",
      "name": "삼성전자",
      "depth": 0
    },
    {
      "companyId": "550e8400-e29b-41d4-a716-446655440011",
      "name": "예시기업",
      "depth": 1
    }
  ],
  "edges": [
    {
      "relationshipId": "550e8400-e29b-41d4-a716-446655440031",
      "sourceCompanyId": "550e8400-e29b-41d4-a716-446655440010",
      "targetCompanyId": "550e8400-e29b-41d4-a716-446655440011",
      "relationshipType": "SUPPLY",
      "score": 82.4,
      "newsScore": 75.0,
      "disclosureScore": 90.0,
      "impactDirection": "POSITIVE"
    }
  ]
}
```
#### Notes
- `depth`는 중심 기업 0, 직접 관계 1, 간접 관계 2·3이다.
- `window`는 응답 관계선에 적용된 집계 기간이다. 기간에 따라 관계선의 점수와 개수가 달라질 수 있다.
- 단계별 흐림 효과는 프론트엔드가 `depth`를 이용해 표현한다.
- `score`는 시스템 기본 비율의 공통 점수다. 사용자가 비율을 조절하면 프론트엔드는 `newsScore`와 `disclosureScore`를 현재 화면에서만 조합한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>경로·깊이 또는 window 값 오류</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>중심 기업 없음</td>
</tr>
<tr>
<td>404</td>
<td>GRAPH_SNAPSHOT_NOT_FOUND</td>
<td>공개된 그래프 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>기업 중심 그래프 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/graphs/latest — 최신 전체 관계 그래프 조회

#### 목적
전체 은하 최초 진입 또는 산업 필터 선택 시 최신 공개 관계 그래프를 조회한다.
#### 인증
불필요. 서버는 시스템 기본 가중치의 공통 점수와 뉴스·공시 구성 점수를 반환한다.
#### Query Params
<table header-row="true">
<tr>
<td>필드</td>
<td>필수</td>
<td>규칙</td>
</tr>
<tr>
<td>universe</td>
<td>아니요</td>
<td>기본 KOSPI100,KOSDAQ (국내 상장사). 허용 값 KOSPI100·KOSDAQ·NASDAQ100, 쉼표로 여러 개</td>
</tr>
<tr>
<td>industryId</td>
<td>아니요</td>
<td>선택 산업에 속한 기업만 조회</td>
</tr>
<tr>
<td>window</td>
<td>아니요</td>
<td>30D·1Y·10Y, 기본 30D</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "snapshotId": "550e8400-e29b-41d4-a716-446655440030",
  "asOfAt": "2026-09-07T06:00:00Z",
  "nextRefreshAt": "2026-09-07T07:00:00Z",
  "window": "30D",
  "personalized": false,
  "nodes": [
    {
      "companyId": "550e8400-e29b-41d4-a716-446655440010",
      "name": "삼성전자",
      "stockCode": "005930",
      "market": "KOSPI",
      "industryName": "반도체",
      "priceChange": {
        "7D": 1.25,
        "30D": -3.42,
        "90D": 7.81
      }
    }
  ],
  "edges": [
    {
      "relationshipId": "550e8400-e29b-41d4-a716-446655440031",
      "sourceCompanyId": "550e8400-e29b-41d4-a716-446655440010",
      "targetCompanyId": "550e8400-e29b-41d4-a716-446655440011",
      "relationshipType": "SUPPLY",
      "score": 82.4,
      "newsScore": 75.0,
      "disclosureScore": 90.0,
      "impactDirection": "POSITIVE"
    }
  ]
}
```
#### Notes
- 전체 그래프의 노드 수에는 고정 상한을 두지 않는다.
- `priceChange`는 기업별 최신 일봉 종가를 기준으로 계산한 7일·30일·90일 등락률(%)이다.
- 휴장일에는 각 기준일 이전의 가장 가까운 거래일 종가를 사용한다. 주가가 없거나 비교 기준 종가가 없으면 해당 값은 `null`이다.
- `priceChange`는 전체 은하의 주가 고도 표현에 사용하며 기업 중심 그래프에는 포함하지 않는다.
- `window`는 응답 관계선에 적용된 집계 기간이다. 기간에 따라 관계선의 점수와 개수가 달라질 수 있다.
- 그래프는 1시간 단위로 검증 후 교체한다.
- 화면은 새 `snapshotId`를 확인한 뒤 사용자 선택으로 갱신할 수 있다.
- 사용자가 조절한 가중치는 같은 탭의 새로고침에서는 유지되며, 탭 세션 종료 후 다시 접속하면 기본 `score`를 사용한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>지원하지 않는 universe·window 또는 잘못된 산업 식별자</td>
</tr>
<tr>
<td>404</td>
<td>GRAPH_SNAPSHOT_NOT_FOUND</td>
<td>공개된 그래프 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>그래프 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/relationships/{relationshipId} — 기업 관계 상세 조회

#### 목적
그래프 관계선 선택 시 양쪽 기업과 관계 유형·점수·영향 방향을 조회한다.
#### 인증
불필요. 서버는 시스템 기본 가중치의 공통 점수와 뉴스·공시 구성 점수를 반환한다.
#### Path Variable
- `relationshipId`: 기업 관계 UUID
#### Query Params
- `window`: 30D·1Y·10Y, 기본 30D
#### Response — 200 OK
```json
{
  "relationshipId": "550e8400-e29b-41d4-a716-446655440031",
  "sourceCompany": {
    "companyId": "550e8400-e29b-41d4-a716-446655440010",
    "name": "삼성전자"
  },
  "targetCompany": {
    "companyId": "550e8400-e29b-41d4-a716-446655440011",
    "name": "예시기업"
  },
  "relationshipType": "SUPPLY",
  "directionality": "DIRECTED",
  "window": "30D",
  "score": 82.4,
  "newsScore": 75.0,
  "disclosureScore": 90.0,
  "impactDirection": "POSITIVE",
  "confidence": 0.88,
  "evidenceCount": 16,
  "asOfAt": "2026-09-07T06:00:00Z",
  "personalized": false
}
```
#### Notes
- 공시는 점수 계산에 사용하지만 응답에서 공시 문서 자체를 공개하지 않는다.
- `newsScore`와 `disclosureScore` 중 한쪽이 `null`이면 프론트엔드는 존재하는 점수를 그대로 사용한다.
- 프론트엔드는 API의 `30D`를 화면에서 `1M`으로 표시하고, 요청 값은 `30D`를 사용한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>relationshipId 또는 window 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>RELATIONSHIP_NOT_FOUND</td>
<td>관계 또는 공개 점수 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>관계 상세 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/relationships/{relationshipId}/evidence — 기업 관계 뉴스 근거 조회

#### 목적
선택한 기업 관계를 뒷받침하는 공개 가능한 뉴스와 대표 근거 문장을 조회한다.
#### 인증
불필요
#### Query Params
- `cursor`: publishedAt + newsId 기반 다음 페이지 커서
- `size`: 기본 10, 최대 20
#### Response — 200 OK
```json
{
  "items": [
    {
      "newsId": "550e8400-e29b-41d4-a716-446655440020",
      "title": "반도체 공급망 협력 확대",
      "summary": "기사 요약",
      "publisher": "예시경제",
      "originalUrl": "https://example.com/news/1",
      "publishedAt": "2026-09-07T02:00:00Z",
      "evidenceSentence": "양사는 공급 협력을 확대한다.",
      "contributionScore": 0.76,
      "confidence": 0.91
    }
  ],
  "nextCursor": null,
  "hasNext": false
}
```
#### Notes
- 기본 정렬은 최신순이다.
- 공시는 내부 계산 근거로만 사용하며 이 API에서 직접 노출하지 않는다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>INVALID_CURSOR</td>
<td>커서 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>RELATIONSHIP_NOT_FOUND</td>
<td>기업 관계 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>관계 근거 조회 중 서버 오류</td>
</tr>
</table>

### events | NEXUS 이벤트

빅카인즈 기사 1건을 이벤트로 보고, 기사에 언급된 기업(근거, `REPORTED`)과 공급망을 타고 추정한 기업(추정, `INFERRED`)을 분리해 돌려준다. 데이터는 NEXUS 파이프라인(`nexus/`)이 V10 테이블에 적재한다. 초과수익률은 이미 관측된 주가 변화이며 미래 예측이 아니다.

투자자용 값(V12 테이블, `nexus cases`·`nexus fundamentals`가 적재)도 함께 돌려준다.
- **뉴스 유형**(`newsType`): 기사를 15개 유형 또는 `OTHER`로 분류한 코드. 과거 사례를 비교 가능하게 묶는 기준이다.
- **방향 딱지**(`signals`, `signalCounts`): 이벤트 × 기업 × 투자 기간(`SHORT` 3거래일 / `LONG` 20거래일)마다 `UP`·`DOWN`·`HOLD`. **모델 예측이 아니라 학습 기간(2025-01 ~ 2026-06)에 같은 뉴스 유형·같은 기업 위치였던 과거 사례의 초과수익률 통계**다. 사례가 10건 이상이고 65% 이상이 같은 방향이며 평균 초과수익률이 충분히 클 때(SHORT 1%, LONG 2%)만 `UP`/`DOWN`이고, 아니면 `HOLD`와 그 이유가 붙는다.
- **적중률**(`GET /api/events/backtest`): 딱지를 학습 기간 데이터로만 만든 뒤 **2026-06-30 이후 뉴스**에서 실제 초과수익률 부호와 맞았는지 잰 값이다.

#### 공통 코드

뉴스 유형(`newsType` → `newsTypeLabel`, `nexus/nexus/cases/newstype.py`와 같다)

| code | label |
| --- | --- |
| ACCIDENT | 사고·공급 차질 |
| EXPORT_CONTROL | 수출 규제·제재 |
| TARIFF | 관세·통상 |
| EARNINGS_BEAT | 실적 호조 |
| EARNINGS_MISS | 실적 부진 |
| SUPPLY_DEAL | 수주·공급 계약 |
| PRICE_UP | 제품 가격 상승 |
| PRICE_DOWN | 제품 가격 하락 |
| PRODUCTION_CUT | 감산·재고 조정 |
| CAPEX | 증설·투자 |
| SHAREHOLDER | 주주환원 |
| AI_DEMAND | AI·HBM 수요 |
| POLICY | 정부 정책·지원 |
| ANALYST | 증권가 전망·목표가 |
| MARKET_WRAP | 증시 시황 |
| OTHER | 기타 |

기업 위치(`position` → `positionLabel`): 기사에 나온 기업이면 `REPORTED`, 추정 기업이면 경로 마지막 단계의 관계다.

| code | label |
| --- | --- |
| REPORTED | 기사에 나온 기업 |
| SUPPLIER | 공급사 |
| CUSTOMER | 고객사 |
| COMPETITOR | 경쟁사 |
| PARTNER | 협력사 |
| INVEST | 지분 관계 |
| COMENTION | 함께 언급 |

방향 딱지 한 건(`signals[]`)

| 필드 | 설명 |
| --- | --- |
| horizon | `SHORT`(3거래일) · `LONG`(20거래일). 목록은 SHORT, LONG 순 |
| windowDays | horizon의 거래일 수(3 · 20) |
| label | `UP` · `DOWN` · `HOLD` |
| holdReason | `HOLD`일 때만: 사례 부족 · 오른 경우와 내린 경우가 비슷함 · 반응이 작음 · 비슷한 유형 없음(유형이 OTHER) · 연결이 약함(추정 HOLD 기업) |
| caseScope | `COMPANY`(이 기업 자신의 과거 사례가 10건 이상이라 그것만 씀) · `POSITION`(같은 위치 기업 전체) · null(비교할 사례 없음) |
| caseCount | 근거가 된 과거 사례 수 |
| sameShare | 다수 방향으로 움직인 사례 비율(0~1) |
| meanAr | 과거 사례의 평균 초과수익률 |
| downShare | 과거 사례 중 내린 비율(0~1) |

#### GET /api/events — 이벤트 목록

#### 목적
이벤트를 최신 출고 시각순으로 조회한다. 취소된 기사(status CANCELLED)는 목록과 상세 모두에서 제외한다.
#### 인증
불필요.
#### Request
```javascript
GET /api/events?valueChain=SEMICONDUCTOR&newsType=EXPORT_CONTROL&hasInferred=true&size=20&cursor={cursor}
```
#### Query Params
<table header-row="true">
<tr>
<td>필드</td>
<td>필수</td>
<td>규칙</td>
</tr>
<tr>
<td>valueChain</td>
<td>예</td>
<td>SEMICONDUCTOR·BATTERY·AUTO</td>
</tr>
<tr>
<td>companyId</td>
<td>아니요</td>
<td>이 기업이 언급되거나 추정 OK로 들어간 이벤트만</td>
</tr>
<tr>
<td>newsType</td>
<td>아니요</td>
<td>이 뉴스 유형의 이벤트만. 공통 코드의 뉴스 유형 코드(대문자 그대로)</td>
</tr>
<tr>
<td>hasInferred</td>
<td>아니요</td>
<td>true면 추정 OK 기업이 1개 이상인 이벤트만. 기본 false</td>
</tr>
<tr>
<td>cursor</td>
<td>아니요</td>
<td>dateline + eventId 기반 커서</td>
</tr>
<tr>
<td>size</td>
<td>아니요</td>
<td>기본 20, 최대 100</td>
</tr>
</table>

#### Response — 200 OK
```json
{
  "items": [
    {
      "eventId": "4238a3dd-5668-5dc2-93f0-8569026fe909",
      "newsId": "10bf0dac-e9d5-5876-a5dd-c376a917ff95",
      "title": "정부 믿고 삼전닉스 샀는데… ‘머니무브’ 씁쓸한 성적표",
      "summary": "본문 앞 200자",
      "publisher": "국민일보",
      "originalUrl": "https://example.com/news/1",
      "dateline": "2026-10-13T17:04:00Z",
      "tradingDate": "2026-10-14",
      "valueChain": "SEMICONDUCTOR",
      "categories": ["경제>증권_증시", "경제>반도체"],
      "reportedCompanies": [
        { "companyId": "928aa9d3-d527-4995-963f-ea928a8fc68b", "name": "SK하이닉스", "stockCode": "000660", "market": "KOSPI" },
        { "companyId": "fd2cbfd5-cd56-49eb-acab-5a7ff13e2fab", "name": "삼성전자", "stockCode": "005930", "market": "KOSPI" }
      ],
      "inferredCount": 0,
      "holdCount": 0,
      "newsType": "MARKET_WRAP",
      "newsTypeLabel": "증시 시황",
      "signalCounts": {
        "SHORT": { "up": 0, "down": 0, "hold": 2 },
        "LONG": { "up": 1, "down": 0, "hold": 1 }
      }
    }
  ],
  "nextCursor": "AAAAAGrOWyAAAAAAAAAAACo66ACHR1fHoPCDqbF8Xbc",
  "hasNext": true
}
```
- `newsId`는 `GET /api/news/{newsId}`와 같은 ID다.
- `tradingDate`는 초과수익률 기준 거래일이다. 15:30 이후 출고면 다음 거래일이고, 아직 정해지지 않았으면 null이다.
- `inferredCount`는 추정·OK, `holdCount`는 추정·판단 보류 기업 수다.
- `newsType`은 뉴스 유형 코드, `newsTypeLabel`은 화면 이름이다. 아직 분류되지 않은 기사는 둘 다 null이다.
- `signalCounts`는 이 이벤트의 OK 영향 기업(언급·추정)에 붙은 방향 딱지 수를 투자 기간별로 센 값이다. 연결이 약해 판단 보류된 기업(영향 HOLD)은 세지 않는다(화면의 '영향 받을 종목' 수와 같다). 키는 `SHORT`·`LONG` 고정이고 딱지가 없으면 모두 0이다.

#### Error
| 상태 | code | 상황 |
| --- | --- | --- |
| 400 | VALIDATION_FAILED | valueChain이 없거나 허용 값이 아님, newsType이 알 수 없는 코드, size가 범위 밖 |
| 400 | INVALID_CURSOR | 커서 형식이 깨짐 |

#### GET /api/events/{eventId} — 이벤트 관계 지도

#### 목적
이벤트 1건의 언급·추정 기업, 추정 경로와 근거 조각, 초과수익률, 기업 위치·방향 딱지·기업 지표를 한 번에 조회한다.
#### 인증
불필요.
#### Response — 200 OK
```json
{
  "event": { "eventId": "…", "title": "…", "tradingDate": "2026-08-31", "newsType": "ACCIDENT", "newsTypeLabel": "사고·공급 차질", "signalCounts": { "SHORT": { "up": 1, "down": 0, "hold": 6 }, "LONG": { "up": 2, "down": 0, "hold": 5 } }, "...": "목록 항목과 같음" },
  "nodes": [
    {
      "companyId": "928aa9d3-d527-4995-963f-ea928a8fc68b",
      "name": "SK하이닉스",
      "stockCode": "000660",
      "market": "KOSPI",
      "basis": "REPORTED",
      "hop": 0,
      "score": 1.0,
      "status": "OK",
      "holdReason": null,
      "impactDirection": null,
      "impactMagnitude": null,
      "confidence": null,
      "anchorCompanyId": null,
      "path": [],
      "abnormalReturns": [
        { "windowDays": 1, "benchmark": "KOSPI", "stockReturn": 0.01270417, "benchmarkReturn": 0.00458691, "abnormalReturn": 0.00811726 },
        { "windowDays": 3, "benchmark": "KOSPI", "stockReturn": -0.02419843, "benchmarkReturn": -0.0333133, "abnormalReturn": 0.00911487 },
        { "windowDays": 5, "benchmark": "KOSPI", "stockReturn": -0.00362976, "benchmarkReturn": -0.01497596, "abnormalReturn": 0.0113462 },
        { "windowDays": 20, "benchmark": "KOSPI", "stockReturn": 0.08287961, "benchmarkReturn": 0.01206826, "abnormalReturn": 0.07081135 }
      ],
      "position": "REPORTED",
      "positionLabel": "기사에 나온 기업",
      "signals": [
        { "horizon": "SHORT", "windowDays": 3, "label": "UP", "holdReason": null, "caseScope": "COMPANY", "caseCount": 121, "sameShare": 0.652893, "meanAr": 0.02026963, "downShare": 0.347107 },
        { "horizon": "LONG", "windowDays": 20, "label": "UP", "holdReason": null, "caseScope": "COMPANY", "caseCount": 121, "sameShare": 0.768595, "meanAr": 0.13697621, "downShare": 0.231405 }
      ],
      "fundamental": {
        "marketCap": 932838750105000, "marketCapRank": 2, "sizeClass": "LARGE",
        "per": null, "pbr": null, "dividendYield": null,
        "netIncomeTtm": null, "equity": null, "dividendPerShare": null,
        "closePrice": 1277000.00, "priceDate": "2026-10-25", "fiscalBasis": null
      }
    },
    {
      "companyId": "d2583bf0-f03f-4863-abd8-f7d1f9b6da94",
      "name": "삼성전기",
      "basis": "INFERRED",
      "hop": 1,
      "status": "HOLD",
      "holdReason": "전이 강도 낮음",
      "anchorCompanyId": "fd2cbfd5-cd56-49eb-acab-5a7ff13e2fab",
      "path": [ { "relationshipType": "PARTNER", "edgeSource": "NEWS", "...": "…" } ],
      "abnormalReturns": [ { "windowDays": 1, "...": "…" } ],
      "position": "PARTNER",
      "positionLabel": "협력사",
      "signals": [
        { "horizon": "SHORT", "windowDays": 3, "label": "HOLD", "holdReason": "연결이 약함", "caseScope": null, "caseCount": 0, "sameShare": null, "meanAr": null, "downShare": null },
        { "horizon": "LONG", "windowDays": 20, "label": "HOLD", "holdReason": "연결이 약함", "caseScope": null, "caseCount": 0, "sameShare": null, "meanAr": null, "downShare": null }
      ],
      "fundamental": null
    }
  ]
}
```
- `nodes`는 REPORTED 먼저(점수 내림차순), 그다음 INFERRED(점수 내림차순)다.
- `status`가 `HOLD`면 `holdReason`(전이 강도 낮음 · 우선순위 밖 · 동시언급만으로 연결)이 함께 온다.
- `anchorCompanyId`는 추정 기업이 퍼져 나온 언급 기업이다.
- `path`의 source·target은 실제 관계 방향이다. 경로의 기업이 기업 마스터에 없으면 id와 이름이 null이다.
- `edgeSource`는 COSMOS(기존 관계 데이터) · NEWS(빅카인즈 근거) · COMENTION(같은 문장 동시언급)이다. `evidence`는 NEWS 단계에만 최대 5건 채워진다. 이벤트 `dateline` 이전 기사를 먼저(이벤트와 가까운 순), 그 뒤 기사를 다음에(이벤트와 가까운 순) 둔다.
- `impactDirection`(POSITIVE·NEUTRAL·NEGATIVE), `impactMagnitude`(0~1, 상승 확률과 하락 확률의 차이), `confidence`(0~1, 가장 큰 클래스의 확률)는 영향 모델(`nexus predict-impact`)이 채운다. 국내 상장사이면서 `status`가 `OK`인 노드만 값이 있다. `confidence`가 있는데 `impactDirection`이 null이면 신뢰도가 표시 기준 미만인 '방향 보류'다. 해외 상장사와 `HOLD` 노드는 셋 다 null이다.
- `abnormalReturns`는 1·3·5·20거래일 초과수익률이다(1 = 이벤트 거래일 당일). 아직 그 기간이 지나지 않았거나 주가가 없으면 해당 기간이 빠진다.
- `position`·`signals`는 방향 딱지가 계산된 노드만 값이 있고, 없으면 `null`·`[]`다. 딱지는 과거 사례 통계이며 예측이 아니다(위 공통 코드 참고).
- `fundamental`은 `GET /api/companies/{companyId}/fundamental`과 같은 객체이고, 기업 지표가 아직 없으면 `null`이다.
- `expectedMoves`는 이 종목의 예상 변동폭(SHORT, LONG 순)이다. 각 항목은 `{ horizon, windowDays, expectedMove, caseCount, grade }`이고, `expectedMove`는 학습 기간 이 종목 뉴스일 뒤 windowDays 거래일 |초과수익률| 평균(방향과 무관), `grade`는 같은 밸류체인 종목을 셋으로 나눈 `SMALL`·`MID`·`LARGE`다. 뉴스 유형과 무관하게 종목마다 하나이며, 아직 없으면 `[]`다. 검증은 `GET /api/events/backtest`의 `moveRows`에 있다.

#### Error
| 상태 | code | 상황 |
| --- | --- | --- |
| 400 | VALIDATION_FAILED | eventId가 UUID 형식이 아님 |
| 404 | EVENT_NOT_FOUND | 이벤트가 없거나 취소됨 |

#### GET /api/events/{eventId}/companies/{companyId}/chart — 뉴스 전후 주가 차트

#### 목적
이벤트의 영향 기업 하나에 대해 이벤트 거래일 앞 5거래일 ~ 뒤 20거래일의 종가와, 같은 기간 시장 지수 대비 누적 수익률을 조회한다.
#### 인증
불필요.
#### Request
```javascript
GET /api/events/cb34cd32-01c4-5314-bee0-321320e1aa03/companies/928aa9d3-d527-4995-963f-ea928a8fc68b/chart
```
#### Response — 200 OK
```json
{
  "eventId": "cb34cd32-01c4-5314-bee0-321320e1aa03",
  "companyId": "928aa9d3-d527-4995-963f-ea928a8fc68b",
  "tradingDate": "2026-08-31",
  "benchmark": "KOSPI",
  "points": [
    { "offset": -5, "date": "2026-08-24", "close": 1671000.0000, "indexClose": 6696.9600, "stockReturn": 0.01088929, "indexReturn": -0.01353979, "abnormalReturn": 0.02442908 },
    { "offset": -1, "date": "2026-08-28", "close": 1653000.0000, "indexClose": 6788.8800, "stockReturn": 0.00000000, "indexReturn": 0.00000000, "abnormalReturn": 0.00000000 },
    { "offset": 0, "date": "2026-08-31", "close": 1674000.0000, "indexClose": 6820.0200, "stockReturn": 0.01270417, "indexReturn": 0.00458691, "abnormalReturn": 0.00811726 },
    { "offset": 2, "date": "2026-09-02", "close": 1613000.0000, "indexClose": 6562.7200, "stockReturn": -0.02419843, "indexReturn": -0.03331330, "abnormalReturn": 0.00911487 },
    { "offset": 20, "date": "2026-09-30", "close": 1783000.0000, "indexClose": 6838.0400, "stockReturn": 0.07864489, "indexReturn": 0.00724125, "abnormalReturn": 0.07140364 }
  ]
}
```
- `benchmark`는 기업 시장(KOSPI·KOSDAQ)의 지수이고, 그 지수에 종가가 있는 날을 거래일 달력으로 쓴다.
- `offset` 0은 이벤트 거래일(`tradingDate`)이다. 그날이 달력에 없으면 그 뒤 첫 거래일이 0이다. `points`는 offset -5 ~ +20, 오래된 순이며 달력이 끝나면(최근 뉴스) 그보다 짧다. 예시는 일부만 보였다.
- 수익률은 offset -1 종가 대비 누적이다: `stockReturn = close / (offset -1 종가) - 1`, `indexReturn`도 지수로 같게, `abnormalReturn = stockReturn - indexReturn`. 그래서 offset 0·2·4·19의 `abnormalReturn`은 노드의 1·3·5·20거래일 초과수익률과 같다.
- 그날 기업 종가가 없으면 `close`·`stockReturn`·`abnormalReturn`이 null이다. offset -1 종가가 없으면 모든 `stockReturn`·`abnormalReturn`이 null이다.

#### Error
| 상태 | code | 상황 |
| --- | --- | --- |
| 400 | VALIDATION_FAILED | eventId·companyId가 UUID 형식이 아님 |
| 404 | EVENT_NOT_FOUND | 이벤트가 없거나 취소됨 |
| 404 | EVENT_COMPANY_NOT_FOUND | 이 기업이 이벤트의 영향 기업(언급·추정, OK·HOLD)이 아님 |
| 404 | EVENT_CHART_UNAVAILABLE | 이벤트 거래일이 아직 없음, 또는 기업이 KOSPI·KOSDAQ 상장사가 아님(지수 없음) |

#### GET /api/events/backtest — 방향 딱지 적중률

#### 목적
방향 딱지가 학습 기간 뒤 뉴스에서 실제로 얼마나 맞았는지 투자 기간 × 뉴스 유형별로 조회한다.
#### 인증
불필요.
#### Request
```javascript
GET /api/events/backtest?valueChain=SEMICONDUCTOR
```
#### Query Params
- `valueChain`: 필수. SEMICONDUCTOR·BATTERY·AUTO
#### Response — 200 OK
```json
{
  "valueChain": "SEMICONDUCTOR",
  "trainEnd": "2026-06-30",
  "testStart": "2026-07-01",
  "testEnd": "2026-10-14",
  "rows": [
    { "horizon": "SHORT", "newsType": null, "newsTypeLabel": null, "caseCount": 10788, "labeledCount": 640, "hitCount": 307, "hitRate": 0.479688, "upLabels": 585, "downLabels": 55, "baseDownShare": 0.484983, "baselineRate": 0.515017 },
    { "horizon": "SHORT", "newsType": "MARKET_WRAP", "newsTypeLabel": "증시 시황", "caseCount": 1860, "labeledCount": 189, "hitCount": 90, "hitRate": 0.476190, "upLabels": 176, "downLabels": 13, "baseDownShare": 0.491935, "baselineRate": 0.508065 },
    { "horizon": "SHORT", "newsType": "CAPEX", "newsTypeLabel": "증설·투자", "caseCount": 860, "labeledCount": 0, "hitCount": 0, "hitRate": null, "upLabels": 0, "downLabels": 0, "baseDownShare": 0.470930, "baselineRate": 0.529070 },
    { "horizon": "LONG", "newsType": null, "newsTypeLabel": null, "caseCount": 8787, "labeledCount": 2752, "hitCount": 1393, "hitRate": 0.506177, "upLabels": 2268, "downLabels": 484, "baseDownShare": 0.414931, "baselineRate": 0.585069 }
  ],
  "moveRows": [
    { "horizon": "SHORT", "windowDays": 3, "grade": "SMALL", "caseCount": 789, "predictedMove": 0.03655162, "realizedMove": 0.03849395 },
    { "horizon": "SHORT", "windowDays": 3, "grade": "MID", "caseCount": 821, "predictedMove": 0.05058233, "realizedMove": 0.05880367 },
    { "horizon": "SHORT", "windowDays": 3, "grade": "LARGE", "caseCount": 841, "predictedMove": 0.06265098, "realizedMove": 0.06907246 }
  ]
}
```
- `moveRows`는 예상 변동폭(방향과 무관한 크기) 검증이다. 같은 채점 기간 뉴스일(같은 날·같은 기업은 1번)에서, 등급(`SMALL`·`MID`·`LARGE`, 종목을 셋으로 나눔)별 예상 변동폭 평균(`predictedMove`)과 실제 |초과수익률| 평균(`realizedMove`)이다. SHORT 먼저, 기간 안에서 SMALL · MID · LARGE 순이다. 화면은 실제 변동폭이 등급 순서대로 커진 기간에만 등급을 붙인다.
- 딱지는 `trainEnd`(2026-06-30)까지의 과거 사례로만 만들고, `testStart` ~ `testEnd`(2026-06-30 이후) 뉴스에서 채점한다. 채점 기간의 정답은 딱지를 만들 때 쓰지 않았다.
- `rows`는 SHORT 먼저, 각 기간 안에서 전체 유형 줄(`newsType` null)이 먼저이고 그다음 `labeledCount` 내림차순이다.
- `caseCount`는 채점 대상 사례 수(HOLD 포함), `labeledCount`는 그중 `UP`·`DOWN` 딱지가 붙은 수, `hitCount`는 딱지 방향과 실제 초과수익률 부호가 맞은 수다.
- `hitRate = hitCount / labeledCount`이고 딱지가 0건이면 null이다.
- `baseDownShare`는 같은 사례에서 실제로 내린 비율, `baselineRate = max(baseDownShare, 1 - baseDownShare)`는 "늘 많은 쪽으로 찍는" 단순 기준선이다. `hitRate`는 이 기준선과 비교해서 보여준다.
- 아직 계산 전인 밸류체인은 `rows`가 `[]`이고 날짜가 모두 null이다.

#### Error
| 상태 | code | 상황 |
| --- | --- | --- |
| 400 | VALIDATION_FAILED | valueChain이 없거나 허용 값이 아님 |

#### GET /api/events/company-history — 기업 과거 뉴스와 주가 반응

#### 목적
기업 한 곳이 영향 기업(OK)으로 들어간 과거 뉴스를 최신순으로 보고, 그때 이 기업의 위치·방향 딱지·실제 초과수익률을 함께 조회한다.
#### 인증
불필요.
#### Request
```javascript
GET /api/events/company-history?companyId=928aa9d3-d527-4995-963f-ea928a8fc68b&size=20&cursor={cursor}
```
#### Query Params
<table header-row="true">
<tr>
<td>필드</td>
<td>필수</td>
<td>규칙</td>
</tr>
<tr>
<td>companyId</td>
<td>예</td>
<td>기업 UUID</td>
</tr>
<tr>
<td>cursor</td>
<td>아니요</td>
<td>dateline + eventId 기반 커서</td>
</tr>
<tr>
<td>size</td>
<td>아니요</td>
<td>기본 20, 최대 100</td>
</tr>
</table>

#### Response — 200 OK
```json
{
  "items": [
    {
      "event": { "eventId": "4238a3dd-5668-5dc2-93f0-8569026fe909", "title": "정부 믿고 삼전닉스 샀는데… ‘머니무브’ 씁쓸한 성적표", "dateline": "2026-10-13T17:04:00Z", "tradingDate": "2026-10-14", "newsType": "MARKET_WRAP", "newsTypeLabel": "증시 시황", "signalCounts": { "SHORT": { "up": 0, "down": 0, "hold": 2 }, "LONG": { "up": 1, "down": 0, "hold": 1 } }, "...": "목록 항목과 같음" },
      "basis": "REPORTED",
      "position": "REPORTED",
      "positionLabel": "기사에 나온 기업",
      "signals": [
        { "horizon": "SHORT", "windowDays": 3, "label": "HOLD", "holdReason": "오른 경우와 내린 경우가 비슷함", "caseScope": "COMPANY", "caseCount": 353, "sameShare": 0.606232, "meanAr": 0.01418501, "downShare": 0.393768 },
        { "horizon": "LONG", "windowDays": 20, "label": "UP", "holdReason": null, "caseScope": "COMPANY", "caseCount": 353, "sameShare": 0.699717, "meanAr": 0.09512769, "downShare": 0.300283 }
      ],
      "abnormalReturns": [
        { "windowDays": 1, "benchmark": "KOSPI", "stockReturn": -0.02129121, "benchmarkReturn": -0.00439743, "abnormalReturn": -0.01689378 },
        { "windowDays": 3, "benchmark": "KOSPI", "stockReturn": -0.13118132, "benchmarkReturn": -0.06085811, "abnormalReturn": -0.07032321 },
        { "windowDays": 5, "benchmark": "KOSPI", "stockReturn": -0.18269231, "benchmarkReturn": -0.07996388, "abnormalReturn": -0.10272843 }
      ]
    }
  ],
  "nextCursor": "AAAAAGrOWyAAAAAAAAAAACo66ACHR1fHoPCDqbF8Xbc",
  "hasNext": true
}
```
- 이 기업의 영향 상태가 `OK`인 이벤트만 나온다(추정 `HOLD`만 있는 이벤트와 취소된 기사는 빠진다). 정렬과 커서는 목록과 같다.
- `event`는 `GET /api/events` 목록 항목과 같은 객체다(`signalCounts`는 그 이벤트의 모든 기업 기준).
- `basis`·`position`·`signals`·`abnormalReturns`는 이 기업 한 곳의 값이다. 딱지가 없으면 `position`이 null, `signals`가 `[]`이고, 초과수익률은 지난 기간만 들어 있다(예시의 최근 기사는 20거래일이 아직 안 지남).
- 없는 기업 ID면 빈 목록을 돌려준다.

#### Error
| 상태 | code | 상황 |
| --- | --- | --- |
| 400 | VALIDATION_FAILED | companyId가 없거나 UUID 형식이 아님, size가 범위 밖 |
| 400 | INVALID_CURSOR | 커서 형식이 깨짐 |

#### GET /api/companies/{companyId}/fundamental — 기업 지표

#### 목적
기업 상세·이벤트 지도 패널에 보여줄 시가총액·PER·PBR·배당수익률·규모를 조회한다. 경로는 companies 도메인이지만 NEXUS 투자자 화면용이라 여기에 적는다. 이벤트 지도 노드의 `fundamental`과 같은 객체다.
#### 인증
불필요.
#### Response — 200 OK
```json
{
  "marketCap": 1500000000000,
  "marketCapRank": 120,
  "sizeClass": "MID",
  "per": 15.2,
  "pbr": 1.8,
  "dividendYield": 0.0167,
  "netIncomeTtm": 98700000000,
  "equity": 833000000000,
  "dividendPerShare": 500.00,
  "closePrice": 30000.00,
  "priceDate": "2026-10-23",
  "fiscalBasis": "최근 4분기 합산"
}
```
(값은 형식을 보이기 위한 예시다.)
- 파이프라인(`nexus fundamentals`)이 OpenDART 재무제표와 시가총액으로 계산한 값이다. `marketCap`·`netIncomeTtm`·`equity`는 원, `dividendYield`는 비율(0.0012 = 0.12%)이다.
- `sizeClass`는 `LARGE`·`MID`·`SMALL`, `marketCapRank`는 시가총액 순위다. `priceDate`는 `closePrice`·`marketCap`의 기준일, `fiscalBasis`는 재무 수치(`netIncomeTtm`·`equity`·`dividendPerShare`)의 기준 기간 설명이다.
- 지표 적재가 단계적으로 진행되므로 시가총액만 있고 PER·PBR 등은 null인 기업도 있다(이벤트 지도 예시 참고).
- 적자라 PER을 계산할 수 없는 경우처럼 개별 값이 null일 수 있다. 지표가 아직 없는 기업은 **200과 함께 모든 값이 null인 객체**를 돌려준다.

#### Error
| 상태 | code | 상황 |
| --- | --- | --- |
| 400 | VALIDATION_FAILED | companyId가 UUID 형식이 아님 |
| 404 | COMPANY_NOT_FOUND | 기업이 없거나 비활성 |



### market | NEXUS 기업 탭 시세

기업 탭 시세판(`/companies`)과 기업 상세(`/companies/:id`)에 쓰는 API다. 모두 공개 GET이다. 등락은 거래일 수로 센다:
1일 = 1거래일 전 종가 대비, 1개월 = 20거래일 전 종가 대비. 비율은 0.0327 = 3.27%다.

#### GET /api/market/indices — 코스피·코스닥 지수

#### 목적
시장 띠의 지수 카드와 기업 상세 차트의 '지수와 비교' 선을 그린다.
#### 인증
불필요.
#### Query Params
| 이름 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| period | 아니오 | 3M | `1M`·`3M`·`6M`·`1Y` = 거래일 20·60·120·250개. 기준점 1개를 더 준다(3M → 61개) |
#### Response — 200 OK
```json
{
  "asOf": "2026-10-23",
  "items": [
    {
      "indexCode": "KOSPI",
      "close": 5533.77,
      "change1d": -0.0102,
      "change1m": 0.0415,
      "points": [{ "date": "2026-07-27", "close": 5011.20 }, { "date": "2026-10-23", "close": 5533.77 }]
    },
    { "indexCode": "KOSDAQ", "close": 764.10, "change1d": 0.0031, "change1m": -0.0122, "points": [] }
  ]
}
```
(값은 형식을 보이기 위한 예시다. `points` 는 실제로는 기간 거래일 수 + 1개.)
- 출처는 `market_index_price`(파이프라인 `nexus cases` 가 FinanceDataReader 로 채운다). `items` 는 KOSPI, KOSDAQ 순이고 행이 없는 지수는 빠진다.
- `asOf` 는 지수가 있는 마지막 거래일, `points` 는 오래된 것부터다. 거래일이 모자라면 `change1d`·`change1m` 은 null.

#### Error
| 상태 | code | 상황 |
| --- | --- | --- |
| 400 | VALIDATION_FAILED | period가 1M·3M·6M·1Y가 아님 |

#### GET /api/companies/quotes — 전 종목 시세

#### 목적
시세판 표 한 번에 그리기. 활성 기업 전부를 페이지 없이 돌려준다(반도체 61종목 + 주가 없는 기업).
#### 인증
불필요.
#### Response — 200 OK
```json
{
  "asOf": "2026-10-23",
  "items": [
    {
      "companyId": "fd2cbfd5-cd56-49eb-acab-5a7ff13e2fab",
      "name": "삼성전자", "stockCode": "005930", "market": "KOSPI", "industry": "반도체 제조",
      "close": 213500.0, "prevClose": 214500.0, "tradingDate": "2026-10-23",
      "change1d": -0.0047, "change1m": 0.0312, "change3m": 0.1544,
      "spark": [207000.0, 213500.0],
      "marketCap": 1259873040024000, "marketCapRank": 1, "sizeClass": "LARGE",
      "per": 8.3592, "pbr": 2.1748, "dividendYield": 0.00774, "dividendPerShare": 1668.00,
      "netIncomeTtm": 150717225000000, "equity": 579309676000000,
      "fiscalBasis": "2025 사업보고서 + 2026 반기 − 2025 반기", "priceDate": "2026-10-25",
      "relationCount": 16,
      "news7d": 33, "newsUsual7d": 3.03, "high52w": 67300.0, "low52w": 29500.0
    }
  ]
}
```
(값은 형식을 보이기 위한 예시다. `spark` 는 실제로는 21개.)
- 등락은 거래일 수로 센다: `change1d` 1거래일, `change1m` 20거래일, `change3m` 60거래일 전 종가 대비. 거래일이 모자라면 null.
- `spark` 는 최근 21개 종가(오래된 것부터, 모자라면 있는 만큼). 주가가 없으면 `close`·`prevClose`·`tradingDate`·`change*` 가 null, `spark` 는 빈 목록.
- 지표 칸(`marketCap` ~ `priceDate`)은 `GET /api/companies/{companyId}/fundamental` 과 같은 값이고, 지표가 없으면 null.
- `industry` 는 대표 업종 이름, `relationCount` 는 공개 은하 스냅샷 1년(1Y) 관계에서 만나는 상대 기업 수다.
- `news7d` 는 마지막 뉴스 날(KST, `news_event` 최신 dateline) 포함 최근 7일 이 기업이 직접 나온(REPORTED) 기사 수, `newsUsual7d` 는 그 앞 60일의 7일 평균이다. `high52w`·`low52w` 는 최근 250거래일 고가 최고·저가 최저(주가 없으면 null). 화면의 '주목 종목'(뉴스 급증·하루 ±5%·52주 끝 5% 이내)은 이 값으로 프론트가 정한다.
- 정렬은 시가총액 내림차순, 시가총액이 없으면 이름순으로 뒤에. `asOf` 는 종목들의 마지막 거래일.

#### GET /api/companies/{companyId}/related — 관계 기업 시세

#### 목적
기업 상세의 '관계 기업 동향'. 공개 은하 스냅샷의 1년(1Y) 관계 기업과 각 기업의 시세를 점수 순으로 돌려준다.
#### 인증
불필요.
#### Response — 200 OK
```json
{
  "companyId": "928aa9d3-d527-4995-963f-ea928a8fc68b",
  "window": "1Y",
  "items": [
    { "relationType": "SUPPLY", "direction": "IN", "score": 82.4, "otherTypes": ["CO_MENTIONED"], "quote": { "companyId": "…", "name": "한미반도체" } },
    { "relationType": "COMPETE", "direction": "NONE", "score": 61.0, "otherTypes": [], "quote": { "companyId": "…", "name": "삼성전자" } }
  ]
}
```
(값은 형식을 보이기 위한 예시다. `quote` 는 `GET /api/companies/quotes` 의 한 종목과 같은 객체다.)
- `relationType` 은 `SUPPLY`·`INVEST`·`PARTNER`·`COMPETE`·`CO_MENTIONED`. `direction` 은 `OUT`(이 기업이 공급·투자하는 쪽), `IN`(상대가 이 기업에 공급·투자하는 쪽), `NONE`(방향 없는 관계).
- 같은 상대와 관계가 여러 개면 점수가 가장 높은 관계가 대표이고, 나머지 종류는 `otherTypes`(점수 순, 중복 없음).
- 관계가 없으면 `items` 는 빈 목록이다.

#### Error
| 상태 | code | 상황 |
| --- | --- | --- |
| 400 | VALIDATION_FAILED | companyId가 UUID 형식이 아님 |
| 404 | COMPANY_NOT_FOUND | 기업이 없거나 비활성 |

### community | 커뮤니티

#### DELETE /api/comments/{commentId} — 기업 커뮤니티 댓글 삭제

#### 목적
로그인 사용자가 자신이 작성한 댓글을 실제 삭제한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Path Variable
- `commentId`: 삭제할 댓글 UUID
#### Response — 204 No Content
본문 없음
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>commentId 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>403</td>
<td>COMMENT_FORBIDDEN</td>
<td>다른 사용자가 작성한 댓글</td>
</tr>
<tr>
<td>404</td>
<td>COMMENT_NOT_FOUND</td>
<td>댓글 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>댓글 삭제 중 서버 오류</td>
</tr>
</table>

#### PATCH /api/comments/{commentId} — 기업 커뮤니티 댓글 수정

#### 목적
로그인 사용자가 자신이 작성한 댓글을 수정한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Path Variable
- `commentId`: 수정할 댓글 UUID
#### Request
```json
{
  "content": "수정한 댓글 내용입니다."
}
```
#### Response — 200 OK
```json
{
  "commentId": "550e8400-e29b-41d4-a716-446655440040",
  "content": "수정한 댓글 내용입니다.",
  "createdAt": "2026-09-07T05:00:00Z",
  "updatedAt": "2026-09-07T05:10:00Z",
  "edited": true
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>내용이 공백이거나 500자를 초과</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>403</td>
<td>COMMENT_FORBIDDEN</td>
<td>다른 사용자가 작성한 댓글</td>
</tr>
<tr>
<td>404</td>
<td>COMMENT_NOT_FOUND</td>
<td>댓글 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>댓글 수정 중 서버 오류</td>
</tr>
</table>

#### GET /api/community/comments — 전체 최신 커뮤니티 댓글 조회

#### 목적
모든 기업 커뮤니티의 댓글을 합쳐 전체 커뮤니티 탭에 최신순으로 제공한다.
#### 인증
불필요
#### Query Params
- `cursor`: createdAt + commentId 기반 다음 페이지 커서
- `size`: 기본 20, 최대 100
#### Response — 200 OK
```json
{
  "items": [
    {
      "commentId": "550e8400-e29b-41d4-a716-446655440040",
      "content": "최근 공급망 뉴스가 인상적이네요.",
      "author": {
        "userId": 1,
        "nickname": "우주탐험가"
      },
      "company": {
        "companyId": "550e8400-e29b-41d4-a716-446655440010",
        "name": "삼성전자",
        "stockCode": "005930",
        "market": "KOSPI"
      },
      "createdAt": "2026-09-07T05:00:00Z",
      "updatedAt": "2026-09-07T05:00:00Z",
      "edited": false
    }
  ],
  "nextCursor": null,
  "hasNext": false
}
```
#### Notes
- `company.stockCode`·`company.market`은 프론트가 기업 로고 파일을 찾는 키다. 종목코드가 없는 기업은 `null`이며 화면은 이니셜로 표시한다.
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>INVALID_CURSOR</td>
<td>커서 형식 오류</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>전체 댓글 조회 중 서버 오류</td>
</tr>
</table>

#### GET /api/companies/{companyId}/comments — 기업별 커뮤니티 댓글 조회

#### 목적
특정 기업 커뮤니티에 작성된 댓글을 최신순으로 조회한다.
#### 인증
불필요
#### Path Variable
- `companyId`: 댓글을 조회할 기업 UUID
#### Query Params
- `cursor`: createdAt + commentId 기반 다음 페이지 커서
- `size`: 기본 20, 최대 100
#### Response — 200 OK
```json
{
  "items": [
    {
      "commentId": "550e8400-e29b-41d4-a716-446655440040",
      "content": "최근 공급망 뉴스가 인상적이네요.",
      "author": {
        "userId": 1,
        "nickname": "우주탐험가"
      },
      "companyId": "550e8400-e29b-41d4-a716-446655440010",
      "createdAt": "2026-09-07T05:00:00Z",
      "updatedAt": "2026-09-07T05:00:00Z",
      "edited": false
    }
  ],
  "nextCursor": null,
  "hasNext": false
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>INVALID_CURSOR</td>
<td>커서 형식 오류</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>기업 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>기업 댓글 조회 중 서버 오류</td>
</tr>
</table>

#### POST /api/companies/{companyId}/comments — 기업 커뮤니티 댓글 작성

#### 목적
로그인 사용자가 특정 기업 커뮤니티에 댓글을 작성한다.
#### 인증
필수 — `Authorization: Bearer {accessToken}`
#### Path Variable
- `companyId`: 댓글을 작성할 기업 UUID
#### Request
```json
{
  "content": "최근 공급망 뉴스가 인상적이네요."
}
```
<table header-row="true">
<tr>
<td>필드</td>
<td>규칙</td>
</tr>
<tr>
<td>content</td>
<td>공백 제외 1~500자</td>
</tr>
</table>
#### Response — 201 Created
```json
{
  "commentId": "550e8400-e29b-41d4-a716-446655440040",
  "companyId": "550e8400-e29b-41d4-a716-446655440010",
  "content": "최근 공급망 뉴스가 인상적이네요.",
  "createdAt": "2026-09-07T05:00:00Z",
  "updatedAt": "2026-09-07T05:00:00Z"
}
```
#### Errors
<table header-row="true">
<tr>
<td>상태</td>
<td>code</td>
<td>언제</td>
</tr>
<tr>
<td>400</td>
<td>VALIDATION_FAILED</td>
<td>내용 길이 또는 companyId 형식 오류</td>
</tr>
<tr>
<td>401</td>
<td>TOKEN_EXPIRED</td>
<td>Access Token 만료</td>
</tr>
<tr>
<td>404</td>
<td>COMPANY_NOT_FOUND</td>
<td>기업 없음</td>
</tr>
<tr>
<td>500</td>
<td>INTERNAL_SERVER_ERROR</td>
<td>댓글 작성 중 서버 오류</td>
</tr>
</table>
