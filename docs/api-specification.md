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
</table>

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
불필요. 유효한 Access Token이 선택적으로 전달되면 `watched`를 함께 반환한다.
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
- 비로그인 응답의 `watched`는 `false`로 반환한다.
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
불필요. 유효한 Access Token이 선택적으로 전달되면 `watched`를 함께 반환한다.
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
- `window`: 7D·30D·90D, 기본 30D
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
7일·30일·90일 기준 기업 지표의 시간별 변화를 차트용으로 조회한다.
#### 인증
불필요
#### Query Params
- `window`: 7D·30D·90D, 기본 30D
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
- 기업이 없는 산업도 반환할지는 구현 단계에서 데이터 정책으로 확정한다.
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
<td>최대</td>
</tr>
<tr>
<td>maxDepth</td>
<td>3</td>
<td>3</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "snapshotId": "550e8400-e29b-41d4-a716-446655440030",
  "asOfAt": "2026-09-07T06:00:00Z",
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
<td>경로 또는 깊이 값 오류</td>
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
<td>기본 KOSPI100,NASDAQ100</td>
</tr>
<tr>
<td>industryId</td>
<td>아니요</td>
<td>선택 산업에 속한 기업만 조회</td>
</tr>
</table>
#### Response — 200 OK
```json
{
  "snapshotId": "550e8400-e29b-41d4-a716-446655440030",
  "asOfAt": "2026-09-07T06:00:00Z",
  "nextRefreshAt": "2026-09-07T07:00:00Z",
  "personalized": false,
  "nodes": [
    {
      "companyId": "550e8400-e29b-41d4-a716-446655440010",
      "name": "삼성전자",
      "stockCode": "005930",
      "market": "KOSPI",
      "industryName": "반도체"
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
<td>지원하지 않는 universe 또는 잘못된 산업 식별자</td>
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
- `window`: 7D·30D·90D, 기본 30D
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
        "name": "삼성전자"
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
