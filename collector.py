# -*- coding: utf-8 -*-
"""
100°10 조달 레이더 - 서버용 수집기 (GitHub Actions 매일 실행)
나라장터 Open API(data.go.kr)에서 대구·경북·밀양 + 관심 키워드 공고를 모아 notices.json 생성.
서비스키는 환경변수 SERVICE_KEY (GitHub Secret)에서 읽습니다. 외부 라이브러리 불필요(표준 라이브러리만).
"""
import os, sys, json, datetime, time
import urllib.parse, urllib.request, urllib.error

KEY = os.environ.get("SERVICE_KEY", "").strip()
if not KEY:
    print("ERROR: 환경변수 SERVICE_KEY 가 없습니다. GitHub Secret 에 SERVICE_KEY 를 등록하세요.")
    sys.exit(1)
# 인코딩(Encoding) 키가 저장돼 있어도 되게: URL-인코딩된 값이면 먼저 디코딩해 원본으로 만든다.
if "%" in KEY and any(tok in KEY for tok in ("%2B", "%2b", "%2F", "%2f", "%3D", "%3d")):
    KEY = urllib.parse.unquote(KEY)

# 입찰공고정보서비스 - 키워드 검색용(PPSSrch) 오퍼레이션
BASES = [
    "https://apis.data.go.kr/1230000/ad/BidPublicInfoService",
    "https://apis.data.go.kr/1230000/BidPublicInfoService",
]
OPS = [
    ("thng",   "getBidPblancListInfoThngPPSSrch",   "물품"),
    ("cnstwk", "getBidPblancListInfoCnstwkPPSSrch", "공사"),
    ("servc",  "getBidPblancListInfoServcPPSSrch",  "용역"),
]

KEYWORDS = ["CCTV", "방범", "방송", "음향", "설계용역"]

def regions_for(kw):
    # 설계용역은 대구·경북, 그 외(CCTV·방범·방송·음향)는 대구·경북·밀양
    if kw == "설계용역":
        return ("대구", "경상북도", "경북")
    return ("대구", "경상북도", "경북", "밀양")

LOOKBACK_DAYS = 30
MAX_PAGES = 15
NUM_ROWS = 100

now = datetime.datetime.now()
BGN = (now - datetime.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d") + "0000"
END = now.strftime("%Y%m%d") + "2359"

def build_url(base, op, kw, page):
    params = {
        "serviceKey": KEY,
        "pageNo": page,
        "numOfRows": NUM_ROWS,
        "type": "json",
        "inqryDiv": 1,
        "inqryBgnDt": BGN,
        "inqryEndDt": END,
        "bidNtceNm": kw,
    }
    qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return base + "/" + op + "?" + qs

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "jodal-radar/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw)

def items_of(j):
    try:
        body = j["response"]["body"]
    except Exception:
        return [], 0
    total = 0
    try:
        total = int(body.get("totalCount", 0) or 0)
    except Exception:
        total = 0
    items = body.get("items")
    if not items:
        return [], total
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return (items or []), total

def region_ok(it, regions):
    blob = " ".join(str(it.get(k, "")) for k in
                     ("ntceInsttNm", "dminsttNm", "rgnLmtBidLocplcNm", "bidNtceNm"))
    return any(r in blob for r in regions)

def to_record(it, cat):
    no = str(it.get("bidNtceNo", "") or "")
    ordn = str(it.get("bidNtceOrd", "") or "000")
    amt_raw = "".join(ch for ch in str(it.get("presmptPrce", "")) if ch.isdigit())
    url = it.get("bidNtceDtlUrl") or (
        "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=%s&bidPbancOrd=%s" % (no, ordn)
    )
    return {
        "id": no,
        "cat": cat,
        "title": it.get("bidNtceNm", "") or "",
        "org": it.get("ntceInsttNm", "") or "",
        "region": it.get("rgnLmtBidLocplcNm", "") or "",
        "amount": int(amt_raw) if amt_raw else 0,
        "posted": (str(it.get("bidNtceDt", "") or ""))[:10],
        "deadline": (str(it.get("bidClseDt", "") or ""))[:10],
        "url": url,
    }

def collect():
    seen = {}
    for cat, op, label in OPS:
        for kw in KEYWORDS:
            regions = regions_for(kw)
            base_ok = None
            page = 1
            while page <= MAX_PAGES:
                data = None
                # 두 베이스 URL 중 되는 쪽 사용
                bases = [base_ok] if base_ok else BASES
                for base in bases:
                    try:
                        data = fetch_json(build_url(base, op, kw, page))
                        base_ok = base
                        break
                    except Exception as e:
                        data = None
                        continue
                if data is None:
                    print("  [%s/%s] 페이지 %d 실패 (건너뜀)" % (label, kw, page))
                    break
                items, total = items_of(data)
                if not items:
                    break
                added = 0
                for it in items:
                    if not region_ok(it, regions):
                        continue
                    rec = to_record(it, cat)
                    if rec["id"] and rec["id"] not in seen:
                        seen[rec["id"]] = rec
                        added += 1
                print("  [%s/%s] p%d: %d건 수신, 지역일치 신규 %d" % (label, kw, page, len(items), added))
                if len(items) < NUM_ROWS:
                    break
                page += 1
                time.sleep(0.2)
    return list(seen.values())

def main():
    print("조회기간: %s ~ %s" % (BGN[:8], END[:8]))
    records = collect()
    # 마감 임박순 정렬(마감 지난 건 뒤로)
    def dkey(r):
        try:
            d = datetime.datetime.strptime(r["deadline"][:10], "%Y-%m-%d").date()
            return (d - now.date()).days
        except Exception:
            return 99999
    records.sort(key=lambda r: (dkey(r) < 0, abs(dkey(r))))
    out = {
        "updated": now.strftime("%Y-%m-%d %H:%M"),
        "source": "조달청 나라장터 · data.go.kr (대구·경북·밀양 한정)",
        "keywords": KEYWORDS,
        "count": len(records),
        "items": records,
    }
    with open("notices.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("완료: 총 %d건 -> notices.json" % len(records))

if __name__ == "__main__":
    main()
