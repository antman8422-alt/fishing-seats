# -*- coding: utf-8 -*-
"""
fetch_seats.py — 인천·경기 낚시배 빈자리 수집 → data/seats.json  (GitHub Actions 용)
- 선상24 공개 API(api.sunsang24.com/ship/list) 날짜별
- 더피싱(thefishing.kr) '서해권 > 경기도' 선박 자동수집 + 월 달력 AJAX
로컬 실행: pip install requests beautifulsoup4 && python fetch_seats.py [--refresh]
"""
import re, sys, json, os, time, datetime as dt, urllib.parse, warnings
import requests
from bs4 import BeautifulSoup


class C:
    DAYS_AHEAD = 60                       # 오늘부터 2달
    SUNSANG24_AREAS = [-405, 511, 2519, 514, 515, -404, 504, 505, 507, 506,   # 인천 + 경기
                       501, 499, 500, 502, 497, 496, 498]                     # 충남 (당진·서산·태안·아산·보령·서천·홍성)
    SUNSANG24_FISH = ""
    THEFISHING_ENABLE = False             # thefishing.kr 는 GitHub 서버 접속 차단 → 페이지에서 Worker 로 실시간 수집
    THEFISHING_LIST_PAGES = 40
    THEFISHING_CACHE = "data/thefishing_boats.json"
    OUTPUT_JSON = "data/seats.json"
    SS_SHIPS = "data/ss_ships.json"       # 선상24 배 목록 (목록 API 에서 본 배를 계속 누적)
    SS_SHIP_WORKERS = 8                   # 배별 월 달력 동시 요청 수
    DEBUG_DUMP = False
    MIN_SEATS = 1
    SPECIES_KEYWORDS = None
    INCLUDE_WEEKDAYS = None
    TARGET_DATES = None

warnings.filterwarnings("ignore")  # SSL 경고 숨김 (verify=False 대체 접속용)
try:
    sys.stdout.reconfigure(encoding="utf-8")  # 윈도우 콘솔 한글 깨짐 방지
except Exception:
    pass

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}
TODAY = dt.date.today()
TARGET = set()
END = TODAY + dt.timedelta(days=C.DAYS_AHEAD)
WD = ["월", "화", "수", "목", "금", "토", "일"]
ERRORS = []


# ── 공통 fetch: https → 실패시 http → verify=False 순으로 시도 ─────────
def fetch(url, timeout=20):
    tries = [url]
    if url.startswith("https://"):
        tries.append("http://" + url[8:])
    elif url.startswith("http://"):
        tries.append("https://" + url[7:])
    last = None
    for u in tries:
        for verify in (True, False):
            try:
                r = requests.get(u, headers=UA, timeout=timeout, verify=verify, allow_redirects=True)
                if r.status_code == 200 and len(r.text) > 500:
                    if "charset" not in r.headers.get("content-type", "").lower():
                        r.encoding = r.apparent_encoding
                    return r.text
                last = f"HTTP {r.status_code}"
                break
            except requests.exceptions.SSLError as e:
                last = f"SSL: {str(e)[:80]}"
                continue          # verify=False 로 재시도
            except Exception as e:
                last = f"{type(e).__name__}: {str(e)[:80]}"
                break
    ERRORS.append((url, last))
    return None


def dump(name, html):
    if not C.DEBUG_DUMP or html is None:
        return
    os.makedirs("debug", exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", name)[:80]
    with open(os.path.join("debug", safe + ".html"), "w", encoding="utf-8") as f:
        f.write(html)


def text_of(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True))


def months_in_range():
    m = TODAY.replace(day=1)
    out = []
    while m <= END:
        out.append(m)
        m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return out


def species_ok(s):
    if not C.SPECIES_KEYWORDS:
        return True
    return any(k in (s or "") for k in C.SPECIES_KEYWORDS)


def weekday_ok(d):
    if not C.INCLUDE_WEEKDAYS:
        return True
    return WD[d.weekday()] in C.INCLUDE_WEEKDAYS


# ══════════════════════════════════════════════════════════
# 1) 선상24 공개 API (api.sunsang24.com/ship/list)  — 지역 전체 선박 자동
# ══════════════════════════════════════════════════════════
def parse_sunsang24_api(date):
    """해당 날짜의 인천·경기(설정 지역) 출항 일정 전부"""
    rows = []
    area = ",".join(str(a) for a in C.SUNSANG24_AREAS)
    for page in range(1, 40):
        url = ("https://api.sunsang24.com/ship/list?type=general&area_type=area"
               f"&area={urllib.parse.quote(area)}&sdate={date.isoformat()}&page={page}"
               f"&fish={urllib.parse.quote(C.SUNSANG24_FISH)}")
        data, last, r = None, "", None
        for attempt in range(4):
            try:
                r = requests.get(url, headers={**UA, "Origin": "https://www.sunsang24.com",
                                               "Referer": "https://www.sunsang24.com/ship/list"}, timeout=30)
                if r.status_code == 405:      # 일정 없는 먼 날짜에 405 를 돌려줌 → 빈 결과
                    data = {"list": []}
                    break
                data = r.json()
                break
            except Exception as e:
                code = getattr(r, "status_code", "?")
                last = f"{type(e).__name__}: {str(e)[:60]} (HTTP {code})"
                time.sleep(3 * (attempt + 1))
        if data is None:
            ERRORS.append((url, last))
            break
        if C.DEBUG_DUMP:
            os.makedirs("debug", exist_ok=True)
            with open(f"debug/ss24_{date.isoformat()}_p{page}.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        lst = data.get("list") or []
        if not lst:
            break
        for x in lst:
            if x.get("sdate") != date.isoformat():
                continue
            ship = x.get("ship") or {}
            left = x.get("remain_embarkation_num")
            cap = x.get("embarkation_num")
            st = x.get("schedule_status_name") or ""
            if st == "예약마감" or x.get("schedule_status_code") == "END":
                left, status = 0, "마감"
            elif left is not None:
                status = "남은자리" if left > 0 else "마감"
            else:
                status = st or "표기없음"
            stime = (x.get("stime") or "")[:5]; etime = (x.get("etime") or "")[:5]
            price = x.get("price") or 0
            rows.append(dict(
                source="선상24", port=x.get("port_name") or ship.get("area_sub") or "",
                boat=ship.get("name") or "", date=date, weekday=WD[date.weekday()], tide="",
                species=(x.get("fish_type") or "")[:40],
                time=f"{stime}~{etime}" if stime else "",
                price=f"{price // 10000}만원" if price else "",
                left=left, capacity=cap, status=status,
                link=f"https://www.sunsang24.com/ship/list/?ship_no={ship.get('no')}&sdate={date.isoformat()}",
                raw=f"{ship.get('area_main','')} {ship.get('area_sub','')} {x.get('fishing_method','')} {st}",
                _ship=[str(ship.get("no")), ship.get("name") or "", x.get("port_name") or ship.get("area_sub") or "",
                       ship.get("area_main") or "", ship.get("area_sub") or ""]))
        if len(lst) < 30:
            break
        time.sleep(0.6)
    return rows


# ══════════════════════════════════════════════════════════
# 1-b) 선상24 배별 월 달력 (schedule_fleet_list)
#      목록 API 는 약 1주 뒤까지만 열려 있어서, 그 뒤 날짜는 배마다 달력을 받아 채움
# ══════════════════════════════════════════════════════════
def ss_ship_month(info, ym, after, ships):
    no, name, port, am, asub = info
    url = f"https://api.sunsang24.com/ship/schedule_fleet_list/{no}/{ym}?simple=&possible=&eyyyymm="
    data, last = None, ""
    for attempt in range(3):
        try:
            r = requests.get(url, headers={**UA, "Origin": "https://www.sunsang24.com",
                                           "Referer": "https://www.sunsang24.com/ship/list"}, timeout=30)
            if r.status_code in (404, 405):
                return []
            data = r.json()
            break
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:60]}"
            time.sleep(2 * (attempt + 1))
    if data is None:
        ERRORS.append((url, last))
        return []
    rows = []
    for x in (data if isinstance(data, list) else []):
        try:
            d = dt.date.fromisoformat(x.get("sdate") or "")
        except ValueError:
            continue
        if d <= after or d > END:
            continue
        left = x.get("remain_embarkation_num")
        if x.get("status_code") == "END" or x.get("status") == "예약마감":
            left = 0
        if left is None:
            continue
        ship = x.get("ship") or {}
        stime = (x.get("stime") or "")[:5]; etime = (x.get("etime") or "")[:5]
        price = x.get("price") or 0
        sno = str(ship.get("no") or no)
        if sno != str(no) and sno in ships:          # 선단 달력에는 같은 선단의 다른 배 일정도 섞여 옴
            _, name, port, am, asub = ships[sno]
        rows.append(dict(_sched=x.get("schedule_no"),
            source="선상24", port=port, date=d, weekday=WD[d.weekday()],
            boat=(ship.get("name") or name) + (f" {x['small_ship_name']}" if x.get("small_ship_name") else ""),
            tide=x.get("tide_water") or "", species=(x.get("fish_type") or "")[:40],
            time=f"{stime}~{etime}" if stime else "",
            price=f"{price // 10000}만원" if price else "",
            left=left, capacity=x.get("embarkation_num"), status="남은자리" if left > 0 else "마감",
            link=f"https://www.sunsang24.com/ship/list/?ship_no={sno}&sdate={d.isoformat()}",
            raw=f"{am} {asub} {x.get('fishing_method') or ''} {x.get('status') or ''}"))
    return rows


def ss_far(ships, after):
    """after 다음 날 ~ END 를 배별 달력으로"""
    from concurrent.futures import ThreadPoolExecutor
    months = sorted({f"{d.year}{d.month:02d}" for d in
                     (after + dt.timedelta(n) for n in range(1, (END - after).days + 1))})
    jobs = [(info, ym) for info in ships.values() for ym in months]
    print(f"선상24 배별 달력: {len(ships)}척 × {len(months)}개월 ({after.isoformat()} 이후) ...")
    out = []
    with ThreadPoolExecutor(C.SS_SHIP_WORKERS) as ex:
        seen = set()
        for got in ex.map(lambda j: ss_ship_month(j[0], j[1], after, ships), jobs):
            for r in got:                              # 같은 일정이 선단 배마다 중복으로 오므로 일정번호로 한 번만
                k = r.pop("_sched", None) or (r["link"], r["time"])
                if k in seen:
                    continue
                seen.add(k)
                out.append(r)
    print(f"  {len(out)}건, 빈자리 {sum(1 for r in out if (r['left'] or 0) >= 1)}건")
    return out


# ══════════════════════════════════════════════════════════
# 2) 더피싱 (thefishing.kr) — 경기도 권역 선박 자동수집 + 월 달력 AJAX
# ══════════════════════════════════════════════════════════
TF = "https://thefishing.kr"
SESSION = requests.Session()
SESSION.headers.update(UA)


def tf_get(url):
    last = ""
    for attempt in range(3):
        u = url if attempt < 2 else url.replace("https://", "http://")
        try:
            r = SESSION.get(u, timeout=60)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:80]}"
        time.sleep(3)
    ERRORS.append((url, last))
    return None


def discover_thefishing(refresh=False):
    """thefishing.kr 예약선박 목록 → '서해권 > 경기도' 배의 uid·이름·항구·st_uid"""
    if os.path.exists(C.THEFISHING_CACHE) and not refresh:
        with open(C.THEFISHING_CACHE, encoding="utf-8") as f:
            cached = json.load(f)
        if cached:
            return cached
        print("더피싱 캐시가 비어 있음 → 다시 수집")
    print("더피싱 선박 목록 수집 중 (thefishing.kr, 경기도 권역) ...")
    boats = {}
    for p in range(1, C.THEFISHING_LIST_PAGES + 1):
        html = tf_get(f"{TF}/reservation/list.php?page={p}")
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        links = soup.find_all("a", href=re.compile(r"reservation/list\.php\?uid=\d+"))
        if not links:
            break
        for a in links:
            uid = re.search(r"uid=(\d+)", a["href"]).group(1)
            t = text_of(a)
            if "서해권 > 경기도" not in re.sub(r"\s*(?:>|&gt;)\s*", " > ", t) or uid in boats:
                continue
            boats[uid] = {"uid": uid, "name": "", "port": "", "st_uid": ""}
        print(f"  page {p}: 누적 경기도권 {len(boats)}척")
        time.sleep(0.3)
    for uid, b in boats.items():
        html = tf_get(f"{TF}/reservation/list.php?uid={uid}")
        if html:
            m = re.search(r"st_uid=(\d+)", html)
            if m:
                b["st_uid"] = m.group(1)
            m2 = re.search(r"<title>\s*\[([^\]]+)\]\s*([^<\-]+?)\s*-", html)
            if m2:
                b["company"] = m2.group(1).strip()
                b["name"] = m2.group(2).strip()
            m3 = re.search(r"서해권\s*(?:>|&gt;)\s*경기도\s*(?:>|&gt;)\s*([가-힣]+)(?:\s*(?:>|&gt;)\s*([가-힣 ]+?항))?", html)
            if m3:
                b["port"] = (m3.group(2) or m3.group(1)).strip()
            if not b["port"] and b.get("company"):
                b["port"] = b["company"].split()[0]
            if not b["name"]:
                b["name"] = f"uid{uid}"
        time.sleep(0.2)
    if not boats:
        print("더피싱 선박 목록 수집 실패 → 캐시 저장 안 함")
        return boats
    with open(C.THEFISHING_CACHE, "w", encoding="utf-8") as f:
        json.dump(boats, f, ensure_ascii=False, indent=1)
    print(f"더피싱 경기도권 {len(boats)}척 (st_uid 확보 {sum(1 for b in boats.values() if b['st_uid'])}) → {C.THEFISHING_CACHE}")
    return boats


RE_TF_SEAT = re.compile(r"남은인원\s*(\d+)\s*명")
RE_TF_PRICE = re.compile(r"(\d{1,3}(?:,\d{3})+)")


def parse_thefishing_month(b, year, month):
    """월 달력 AJAX → 날짜별 남은인원"""
    rows = []
    if not b.get("st_uid"):
        return rows
    url = f"{TF}/reservation/list.view.view.1.ajax.new.php"
    try:
        r = SESSION.post(url, data={"date6": f"{year}{month:02d}", "st_uid": b["st_uid"], "pa_uid": b["uid"]},
                         headers={"Referer": f"{TF}/reservation/list.php?uid={b['uid']}",
                                  "X-Requested-With": "XMLHttpRequest"}, timeout=60)
        r.encoding = "utf-8"
        html = r.text
    except Exception as e:
        ERRORS.append((url + f" uid={b['uid']}", f"{type(e).__name__}: {str(e)[:80]}"))
        return rows
    dump(f"tf_{b['uid']}_{year}{month:02d}", html)
    soup = BeautifulSoup(html, "html.parser")
    for td in soup.find_all("td"):
        day_el = td.find("span", class_="day")
        if not day_el:
            continue
        try:
            d = dt.date(year, month, int(text_of(day_el)))
        except ValueError:
            continue
        t = text_of(td)
        tide_el = td.find("span", class_="wa")
        tide = text_of(tide_el) if tide_el else ""
        seat = RE_TF_SEAT.search(t)
        if seat:
            left, status = int(seat.group(1)), "남은자리"
        elif "예약마감" in t or "예약완료" in t:
            left, status = 0, "마감"
        else:
            continue  # 출조 없는 날
        # 어종/요금: '공지' 뒤 첫 단어, 마지막 '대100,000' 패턴
        li = td.select("ul.schedule2 li")
        sp_txt = " / ".join(re.sub(r"\s*(대|소)?\s*[\d,]+$", "", text_of(x)).strip() for x in li) if li else ""
        if not sp_txt:
            m_sp = re.search(r"공지\s*(\S+)", t)
            sp_txt = m_sp.group(1) if m_sp else ""
        pr = RE_TF_PRICE.findall(t)
        rows.append(dict(
            source="더피싱", port=b["port"], boat=b["name"], date=d, weekday=WD[d.weekday()], tide=tide,
            species=sp_txt[:40], time="",
            price=f"{int(pr[-1].replace(',', '')) // 10000}만원" if pr else "",
            left=left, capacity=None, status=status,
            link=f"{TF}/reservation/list.php?uid={b['uid']}", raw=t[:160]))
    return rows


# ══════════════════════════════════════════════════════════
# 4) JSON 출력
# ══════════════════════════════════════════════════════════
def main():
    refresh = "--refresh" in sys.argv or not os.path.exists(C.THEFISHING_CACHE)
    rows, boatlist = [], []
    dates = [TODAY + dt.timedelta(d) for d in range(C.DAYS_AHEAD + 1)]

    print(f"선상24 API: {len(dates)}일 스캔 ...")
    empty_run = 0
    for d in dates:
        if empty_run >= 3:
            break
        got = parse_sunsang24_api(d)
        empty_run = empty_run + 1 if not got else 0
        rows += got
        print(f"  {d.isoformat()}({WD[d.weekday()]}) {len(got):>3}척, 빈자리 {sum(1 for r in got if (r['left'] or 0) >= 1)}척")
        time.sleep(0.25)

    # 선상24 배 목록 누적 → 목록 API 가 안 열린 날짜는 배별 달력으로
    ships = {}
    if os.path.exists(C.SS_SHIPS):
        try:
            with open(C.SS_SHIPS, encoding="utf-8") as f:
                ships = json.load(f)
        except Exception:
            ships = {}
    for r in rows:
        info = r.pop("_ship", None)
        if info and info[0] not in ("None", ""):
            ships[info[0]] = info
    os.makedirs(os.path.dirname(C.SS_SHIPS), exist_ok=True)
    with open(C.SS_SHIPS, "w", encoding="utf-8") as f:
        json.dump(ships, f, ensure_ascii=False, separators=(",", ":"))
    list_max = max([r["date"] for r in rows] or [TODAY - dt.timedelta(1)])
    if ships and list_max < END:
        rows += ss_far(ships, list_max)

    if C.THEFISHING_ENABLE:
        tf = discover_thefishing(refresh)
        months = sorted({(d.year, d.month) for d in dates})
        print(f"더피싱 {len(tf)}척 × {len(months)}개월 스캔 ...")
        for b in tf.values():
            n0 = len(rows)
            for y, mo in months:
                rows += parse_thefishing_month(b, y, mo)
            print(f"  {b['name'][:20]:<22} {len(rows) - n0:>3}일")
            time.sleep(0.2)
        boatlist = [dict(source="더피싱", port=b["port"], boat=b["name"],
                         link=f"{TF}/reservation/list.php?uid={b['uid']}") for b in tf.values()]

    rows = [r for r in rows if TODAY <= r["date"] <= END]
    rows.sort(key=lambda r: (r["date"], -(r["left"] if r["left"] is not None else -1), r["port"], r["boat"]))
    out = {
        "updated": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST"),
        "from": TODAY.isoformat(), "to": END.isoformat(), "ss_list_max": list_max.isoformat(),
        "rows": [{**r, "date": r["date"].isoformat(), "raw": r["raw"][:80]} for r in rows],
        "boats": boatlist,
        "errors": ERRORS[:50],
    }
    os.makedirs(os.path.dirname(C.OUTPUT_JSON), exist_ok=True)
    with open(C.OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n전체 {len(rows)}건 / 빈자리 {sum(1 for r in rows if (r['left'] or 0) >= 1)}건 / 오류 {len(ERRORS)}건 → {C.OUTPUT_JSON}")


if __name__ == "__main__":
    main()
