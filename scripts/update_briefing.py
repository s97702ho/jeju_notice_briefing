import argparse
import html
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

INCLUDE_PATTERN = re.compile(
    r"도시계획|도시관리|도시개발|실시계획|지구단위|지형도면|개발사업|입지\s*결정|토지이용|용도지역|용도지구|정비계획|택지|유원지|관광개발"
)
EXCLUDE_PATTERN = re.compile(
    r"도로점용|하천점용|비굴착|차집관로|안전점검|평가위원|무단방치|위반건축물|시정명령|공시송달|도로명주소|소액수의|견적제출|자동차보험|노거수|담배소매"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
}


def fetch_url(url: str, data: bytes | None = None, referer: str | None = None) -> str:
    req_headers = dict(HEADERS)
    if referer:
        req_headers["Referer"] = referer
    req = urllib.request.Request(url, data=data, headers=req_headers)
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        raw = resp.read()
        for enc in ("utf-8", "cp949", "euc-kr"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")


def clean_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_html_lines(raw_html: str) -> list[str]:
    if not raw_html:
        return []
    s = html.unescape(raw_html)
    s = re.sub(r"<script[^>]*>.*?</script>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<style[^>]*>.*?</style>", " ", s, flags=re.DOTALL | re.IGNORECASE)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</(p|div|tr|li|h\d)>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<[^>]+>", " ", s)
    lines = []
    for line in s.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def parse_date(s: str):
    m = re.search(r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})", s or "")
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=KST).date()
    except ValueError:
        return None


def format_korean_date(s: str, fallback_date) -> str:
    dt = parse_date(s)
    if dt:
        return f"{dt.year}. {dt.month:02d}. {dt.day:02d}."
    return f"{fallback_date.year}. {fallback_date.month:02d}. {fallback_date.day:02d}."


def is_target_notice(title: str) -> bool:
    if not title:
        return False
    if EXCLUDE_PATTERN.search(title):
        return False
    return bool(INCLUDE_PATTERN.search(title))


def get_tag(title: str, suffix: str = "") -> str:
    base = "도시·개발"
    if re.search(r"도시계획시설|실시계획", title):
        base = "도시계획시설·실시계획"
    elif re.search(r"지형도면|용도지역|토지이용", title):
        base = "지형도면·토지규제"
    elif re.search(r"입지\s*결정", title):
        base = "기반시설·입지결정"
    elif re.search(r"지구단위", title):
        base = "지구단위계획"
    elif re.search(r"개발사업|도시개발", title):
        base = "개발사업"
    return f"{base} ({suffix})" if suffix else base


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3)
    args = parser.parse_args()

    now = datetime.now(KST)
    today_date = now.date()
    cutoff_date = today_date - timedelta(days=args.days)
    today_key = today_date.strftime("%Y-%m-%d")

    collected = []
    seen_keys = set()

    def add_item(item: dict):
        key = f"{item['agency']}|{item['noticeNo']}|{item['title']}"
        if key not in seen_keys:
            seen_keys.add(key)
            collected.append(item)

    # 1. 제주특별자치도청 고시·공고
    try:
        for page in range(1, 4):
            raw = fetch_url(f"https://www.jeju.go.kr/tool/sido/api.jsp?act=index&page={page}")
            data = json.loads(raw)
            for g in data.get("gosis", []):
                dt = parse_date(g.get("date", ""))
                if dt and dt < cutoff_date:
                    continue
                title = clean_text(g.get("title", ""))
                if is_target_notice(title):
                    gb = g.get("gb", "")
                    cat = "고시" if gb == "N" else ("공고" if gb == "A" else gb)
                    add_item({
                        "agency": "제주특별자치도",
                        "tag": get_tag(title),
                        "noticeNo": f"{g.get('gosiNo', '')} ({cat})",
                        "title": title,
                        "dept": clean_text(g.get("dept", "")),
                        "date": format_korean_date(g.get("date", ""), today_date),
                        "summary": f"{title} 관련 {cat}입니다. 세부 필지, 면적, 도면 등은 원문에서 확인하십시오.",
                        "url": f"https://www.jeju.go.kr/news/news/law.htm?act=view&gb={gb}&no={g.get('no', '')}",
                    })
    except Exception as e:
        print(f"[WARN] 제주특별자치도 고시·공고 조회 오류: {e}")

    # 2. 제주특별자치도보 (도보)
    try:
        dobo_html = fetch_url("https://www.jeju.go.kr/news/online/list/2014.htm")
        checked_seqs = set()
        for m in re.finditer(r'<a[^>]+href="([^"]*seq=(\d+)[^"]*)"[^>]*>(.*?)</a>', dobo_html, re.DOTALL):
            href = html.unescape(m.group(1))
            seq = m.group(2)
            if seq in checked_seqs:
                continue
            checked_seqs.add(seq)

            start_idx = max(0, m.start() - 200)
            ctx = dobo_html[start_idx : m.start() + 600]
            dt_m = re.search(r"(\d{4}[-.]\d{1,2}[-.]\d{1,2})", ctx)
            post_date_str = dt_m.group(1) if dt_m else ""
            post_dt = parse_date(post_date_str)
            if post_dt and post_dt < cutoff_date:
                continue

            detail_url = href if href.startswith("http") else f"https://www.jeju.go.kr/news/online/list/2014.htm?act=view&seq={seq}"
            detail_html = fetch_url(detail_url)
            if not post_dt:
                d2 = re.search(r"(\d{4}[-.]\d{1,2}[-.]\d{1,2})", detail_html)
                if d2:
                    post_date_str = d2.group(1)
                    post_dt = parse_date(post_date_str)
                    if post_dt and post_dt < cutoff_date:
                        continue

            for line in clean_html_lines(detail_html):
                if len(line) < 10 or len(line) > 220:
                    continue
                if is_target_notice(line):
                    no_m = re.search(r"(제?\s*20\d{2}\s*[-년]\s*\d+\s*호?)", line)
                    notice_no = f"{re.sub(r'\s+', '', no_m.group(1))} (도보)" if no_m else f"도보 수록 (seq:{seq})"
                    add_item({
                        "agency": "제주특별자치도",
                        "tag": get_tag(line, "도보"),
                        "noticeNo": notice_no,
                        "title": line,
                        "dept": "제주특별자치도보 (세부 부서 원문 확인)",
                        "date": format_korean_date(post_date_str, today_date),
                        "summary": "제주특별자치도보에 수록된 항목입니다. 세부 내용 및 첨부 PDF는 원문 도보 페이지에서 확인하십시오.",
                        "url": detail_url,
                    })
    except Exception as e:
        print(f"[WARN] 제주특별자치도보 조회 오류: {e}")

    # 3. 제주시청
    try:
        for page in range(1, 4):
            page_html = fetch_url(f"https://www.jejusi.go.kr/information/intro/notice.do?currentPageNo={page}")
            tbody_m = re.search(r"<tbody>(.*?)</tbody>", page_html, re.DOTALL)
            if not tbody_m:
                continue
            for row_m in re.finditer(r"<tr>(.*?)</tr>", tbody_m.group(1), re.DOTALL):
                row_html = row_m.group(1)
                no_m = re.search(r'<td class="number2">(.*?)</td>', row_html, re.DOTALL)
                title_m = re.search(r'<td class="title">.*?<a href="([^"]+)">(.*?)</a>', row_html, re.DOTALL)
                dept_m = re.search(r'<td class="writer">(.*?)</td>', row_html, re.DOTALL)
                date_m = re.search(r'<td class="date">(\d{4}-\d{2}-\d{2})</td>', row_html, re.DOTALL)
                if title_m and date_m:
                    date_str = date_m.group(1)
                    dt = parse_date(date_str)
                    if dt and dt < cutoff_date:
                        continue
                    title = clean_text(title_m.group(2))
                    if is_target_notice(title):
                        href = clean_text(title_m.group(1))
                        add_item({
                            "agency": "제주시",
                            "tag": get_tag(title),
                            "noticeNo": clean_text(no_m.group(1)) if no_m else "",
                            "title": title,
                            "dept": clean_text(dept_m.group(1)) if dept_m else "",
                            "date": format_korean_date(date_str, today_date),
                            "summary": f"{title} 관련 고시·공고입니다. 세부 내용은 원문에서 확인하십시오.",
                            "url": f"https://www.jejusi.go.kr{href}",
                        })
    except Exception as e:
        print(f"[WARN] 제주시청 조회 오류: {e}")

    # 4. 서귀포시청
    try:
        form_data = urllib.parse.urlencode({
            "jndinm": "OfrNotAncmtEJB",
            "context": "NTIS",
            "method": "selectListOfrNotAncmt",
            "methodnm": "selectListOfrNotAncmtHomepage",
            "homepage_pbs_yn": "Y",
            "subCheck": "N",
            "ofr_pageSize": "40",
            "not_ancmt_se_code": "01,04,07",
            "countYn": "Y",
            "pageIndex": "1",
        }).encode("utf-8")
        sgp_html = fetch_url(
            "https://eminwon.seogwipo.go.kr/emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do",
            data=form_data,
            referer="https://eminwon.seogwipo.go.kr/emwp/jsp/ofr/OfrNotAncmtL.jsp?not_ancmt_se_code=01,04,07",
        )
        for row_m in re.finditer(r"<tr[^>]*>\s*<td[^>]*><a[^>]*searchDetail\('(\d+)'\)[^>]*>.*?</tr>", sgp_html, re.DOTALL):
            mgt_no = row_m.group(1)
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row_m.group(0), re.DOTALL)
            if len(tds) >= 5:
                notice_no = clean_text(tds[1])
                title = clean_text(tds[2])
                dept = clean_text(tds[3])
                date_str = clean_text(tds[4])
                dt = parse_date(date_str)
                if dt and dt < cutoff_date:
                    continue
                if is_target_notice(title):
                    add_item({
                        "agency": "서귀포시",
                        "tag": get_tag(title),
                        "noticeNo": notice_no,
                        "title": title,
                        "dept": dept,
                        "date": format_korean_date(date_str, today_date),
                        "summary": f"{title} 관련 고시·공고입니다. (서귀포시청 관리번호: {mgt_no})",
                        "url": "https://www.seogwipo.go.kr/info/news/law.htm",
                    })
    except Exception as e:
        print(f"[WARN] 서귀포시청 조회 오류: {e}")

    # 기존 briefing_data.js 읽기 및 병합
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_js_path = os.path.join(root_dir, "briefing_data.js")
    existing_data = {}
    if os.path.exists(data_js_path):
        with open(data_js_path, "r", encoding="utf-8") as f:
            raw_js = f.read()
        json_part = re.sub(r"^\s*window\.JEJU_BRIEFING_DATA\s*=\s*", "", raw_js)
        json_part = re.sub(r";\s*$", "", json_part)
        try:
            existing_data = json.loads(json_part)
        except json.JSONDecodeError:
            existing_data = {}

    range_label = (
        f"{today_date.year}. {today_date.month:02d}. {today_date.day:02d}. "
        f"(조회 범위: {cutoff_date.month:02d}. {cutoff_date.day:02d}. ~ {today_date.month:02d}. {today_date.day:02d}.)"
    )
    existing_data[today_key] = {
        "label": range_label,
        "notices": collected,
    }

    with open(data_js_path, "w", encoding="utf-8") as f:
        f.write("window.JEJU_BRIEFING_DATA = ")
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"[OK] {today_key} 업데이트 완료: 총 {len(collected)}건 저장")


if __name__ == "__main__":
    main()
