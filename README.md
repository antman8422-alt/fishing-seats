# 낚시배 빈자리 (인천·경기)

GitHub Actions가 2시간마다 선상24·더피싱에서 남은 자리를 모아 `data/seats.json`에 저장하고,
`index.html`이 그걸 폰에서 보여줍니다.

## 설정

- Settings → Pages → Deploy from a branch → main / (root)
- Actions 탭 → scan-seats → Run workflow 로 수동 실행
- 주소: https://antman8422-alt.github.io/fishing-seats/

## 바꾸고 싶을 때

- 수집 주기: `.github/workflows/scan.yml` 의 `cron` (현재 2시간)
- 앞으로 며칠치: `fetch_seats.py` 맨 위 `class C` 의 `DAYS_AHEAD = 28`
- 지역 추가(충남 등): `SUNSANG24_AREAS` 에 코드 추가
- 더피싱 선박 목록은 매주 월요일 자동 재수집

## 로컬 실행

```
pip install requests beautifulsoup4
python fetch_seats.py
```
