import argparse
import html
import json
import os
import re
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

INCLUDE_PATTERN = re.compile(
    r"도시계획|도시관리|도시개발|실시계획|지구단위|지형도면|개발사업|입지\s*결정|토지이용|용도지역|용도지구|정비계획|정비사업|도시정비|재건축|재개발|민간특례|민간공원|특례사업|택지|유원지|관광개발"
)
EXCLUDE_PATTERN = re.compile(
    r"도로점용|하천점용|비굴착|차집관로|하수관로\s*정비|폐기물\s*처리용역|입찰공고|안전점검|평가위원|무단방치|위반건축물|시정명령|공시송달|도로명주소|소액수의|견적제출|자동차보험|노거수|담배소매"
)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def fetch_url(url: str, data: dict | None = None, referer: str | None = None) -> str:
    cmd = ["curl", "-4", "-L", "-s", "-m", "20", "-A", UA]
    if referer:
        cmd.extend(["-e", referer])
    if data:
        cmd.extend(["-X", "POST", "-d", urllib.parse.urlencode(data)])
    cmd.append(url)

    last_err = None
    for attempt in range(2):
        res = subprocess.run(cmd, capture_output=True)
        if res.returncode == 0 and res.stdout:
            raw = res.stdout
            for enc in ("utf-8", "cp949", "euc-kr"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
        last_err = res.stderr.decode("utf-8", errors="replace").strip() or f"curl exit {res.returncode}"
        time.sleep(1.0)
    raise RuntimeError(last_err)


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


def format_korean_date(dt, fallback_date) -> str:
    target = dt or fallback_date
    return f"{target.year}. {target.month:02d}. {target.day:02d}."


def is_target_notice(title: str) -> bool:
    if not title:
        return False
    if EXCLUDE_PATTERN.search(title):
        return False
    return bool(INCLUDE_PATTERN.search(title))


def get_tag(title: str, suffix: str = "") -> str:
    base = "도시·개발"
    if re.search(r"민간특례|민간공원|특례사업", title):
        base = "민간특례"
    elif re.search(r"재건축|재개발|정비사업|도시정비|정비계획", title):
        base = "재개발·재건축·정비"
    elif re.search(r"도시계획시설|실시계획", title):
        base = "도시계획시설·실시계획"
    elif re.search(r"지형도면|용도지역|용도지구|토지이용", title):
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
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--archive-days", type=int, default=30)
    args = parser.parse_args()

    now = datetime.now(KST)
    today_date = now.date()
    cutoff_recent = today_date - timedelta(days=args.days)
    cutoff_archive = today_date - timedelta(days=args.archive_days)
    today_key = today_date.strftime("%Y-%m-%d")

    recent_items = []
    archive_items = []
    seen_keys = set()
    error_count = 0

    def add_item(item: dict, item_date):
        key = f"{item['agency']}|{item['noticeNo']}|{item['title']}"
        if key in seen_keys:
            return
        seen_keys.add(key)
        effective_date = item_date or today_date
        if effective_date >= cutoff_recent:
            recent_items.append(item)
        elif effective_date >= cutoff_archive:
            archive_items.append(item)

    # 1. 제주도청 고시·공고 (최대 8페이지 + 요청 간 0.4초 대기)
    try:
        for page in range(1, 9):
            raw = fetch_url(f"https://www.jeju.go.kr/tool/sido/api.jsp?act=index&page={page}")
            data = json.loads(raw)
            gosis = data.get("gosis", [])
            if not gosis:
                break
            page_has_valid = False
            for g in gosis:
                dt = parse_date(g.get("date", ""))
                if dt and dt < cutoff_archive:
                    continue
                page_has_valid = True
                title = clean_text(g.get("title", ""))
                if is_target_notice(title):
                    gb = g.get("gb", "")
                    cat = "고시" if gb == "N" else ("공고" if gb == "A" else gb)
                    add_item({
                        "agency": "제주도청",
                        "tag": get_tag(title),
                        "noticeNo": f"{g.get('gosiNo', '')} ({cat})",
                        "title": title,
                        "dept": clean_text(g.get("dept", "")),
                        "date": format_korean_date(dt, today_date),
                        "isoDate": (dt or today_date).strftime("%Y-%m-%d"),
                        "summary": f"{title} 관련 {cat}입니다. 세부 필지, 면적, 도면 등은 원문에서 확인하십시오.",
                        "url": f"https://www.jeju.go.kr/news/news/law/jeju2.htm#{gb}_{g.get('no', '')}",
                    }, dt)
            if not page_has_valid:
                break
            time.sleep(0.4)
    except Exception as e:
        error_count += 1
        print(f"[WARN] 제주도청 고시·공고 조회 오류: {e}")

    # 2. 제주특별자치도보 (도보)
    try:
        checked_seqs = set()
        dobo_html = fetch_url("https://www.jeju.go.kr/news/online/list/2014.htm")
        for m in re.finditer(r'<a[^>]+href="([^"]*seq=(\d+)[^"]*)"[^>]*>(.*?)</a>', dobo_html, re.DOTALL):
            href = html.unescape(m.group(1))
            seq = m.group(2)
            if seq in checked_seqs:
                continue
            checked_seqs.add(seq)

            start_idx = max(0, m.start() - 200)
            ctx = dobo_html[start_idx : m.start() + 600]
            dt_m = re.search(r"(\d{4}[-.]\d{1,2}[-.]\d{1,2})", ctx)
            post_dt = parse_date(dt_m.group(1)) if dt_m else None
            if post_dt and post_dt < cutoff_archive:
                continue

            time.sleep(0.4)
            detail_url = href if href.startswith("http") else f"https://www.jeju.go.kr/news/online/list/2014.htm?act=view&seq={seq}"
            detail_html = fetch_url(detail_url)
            if not post_dt:
                d2 = re.search(r"(\d{4}[-.]\d{1,2}[-.]\d{1,2})", detail_html)
                if d2:
                    post_dt = parse_date(d2.group(1))
                    if post_dt and post_dt < cutoff_archive:
                        continue

            for line in clean_html_lines(detail_html):
                if len(line) < 10 or len(line) > 220:
                    continue
                if is_target_notice(line):
                    no_m = re.search(r"(제?\s*20\d{2}\s*[-년]\s*\d+\s*호?)", line)
                    if no_m:
                        cleaned_no = re.sub(r"\s+", "", no_m.group(1))
                        notice_no = f"{cleaned_no} (도보)"
                    else:
                        notice_no = f"도보 수록 (seq:{seq})"
                    add_item({
                        "agency": "제주도청",
                        "tag": get_tag(line, "도보"),
                        "noticeNo": notice_no,
                        "title": line,
                        "dept": "제주특별자치도보 (세부 부서 원문 확인)",
                        "date": format_korean_date(post_dt, today_date),
                        "isoDate": (post_dt or today_date).strftime("%Y-%m-%d"),
                        "summary": "제주특별자치도보에 수록된 항목입니다. 세부 내용 및 첨부 PDF는 원문 도보 페이지에서 확인하십시오.",
                        "url": detail_url,
                    }, post_dt)
    except Exception as e:
        error_count += 1
        print(f"[WARN] 제주특별자치도보 조회 오류: {e}")

    # 3. 제주시청 (최대 8페이지 + 0.4초 대기)
    try:
        for page in range(1, 9):
            page_html = fetch_url(f"https://www.jejusi.go.kr/information/intro/notice.do?currentPageNo={page}")
            tbody_m = re.search(r"<tbody>(.*?)</tbody>", page_html, re.DOTALL)
            if not tbody_m:
                break
            page_has_valid = False
            for row_m in re.finditer(r"<tr>(.*?)</tr>", tbody_m.group(1), re.DOTALL):
                row_html = row_m.group(1)
                no_m = re.search(r'<td class="number2">(.*?)</td>', row_html, re.DOTALL)
                title_m = re.search(r'<td class="title">.*?<a href="([^"]+)">(.*?)</a>', row_html, re.DOTALL)
                dept_m = re.search(r'<td class="writer">(.*?)</td>', row_html, re.DOTALL)
                date_m = re.search(r'<td class="date">(\d{4}-\d{2}-\d{2})</td>', row_html, re.DOTALL)
                if title_m and date_m:
                    dt = parse_date(date_m.group(1))
                    if dt and dt < cutoff_archive:
                        continue
                    page_has_valid = True
                    title = clean_text(title_m.group(2))
                    if is_target_notice(title):
                        href = clean_text(title_m.group(1))
                        detail_url = urllib.parse.urljoin("https://www.jejusi.go.kr/information/intro/notice.do", href)
                        add_item({
                            "agency": "제주시",
                            "tag": get_tag(title),
                            "noticeNo": clean_text(no_m.group(1)) if no_m else "",
                            "title": title,
                            "dept": clean_text(dept_m.group(1)) if dept_m else "",
                            "date": format_korean_date(dt, today_date),
                            "isoDate": (dt or today_date).strftime("%Y-%m-%d"),
                            "summary": f"{title} 관련 고시·공고입니다. 세부 내용은 원문에서 확인하십시오.",
                            "url": detail_url,
                        }, dt)
            if not page_has_valid:
                break
            time.sleep(0.4)
    except Exception as e:
        error_count += 1
        print(f"[WARN] 제주시청 조회 오류: {e}")

    # 4. 서귀포시청 (최대 3페이지, 50건씩)
    try:
        for page in range(1, 4):
            form_data = {
                "jndinm": "OfrNotAncmtEJB",
                "context": "NTIS",
                "method": "selectListOfrNotAncmt",
                "methodnm": "selectListOfrNotAncmtHomepage",
                "homepage_pbs_yn": "Y",
                "subCheck": "N",
                "ofr_pageSize": "50",
                "not_ancmt_se_code": "01,04,07",
                "countYn": "Y",
                "pageIndex": str(page),
            }
            sgp_html = fetch_url(
                "https://eminwon.seogwipo.go.kr/emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do",
                data=form_data,
                referer="https://eminwon.seogwipo.go.kr/emwp/jsp/ofr/OfrNotAncmtL.jsp?not_ancmt_se_code=01,04,07",
            )
            page_has_valid = False
            for row_m in re.finditer(r"<tr[^>]*>\s*<td[^>]*><a[^>]*searchDetail\('(\d+)'\)[^>]*>.*?</tr>", sgp_html, re.DOTALL):
                mgt_no = row_m.group(1)
                tds = re.findall(r"<td[^>]*>(.*?)</td>", row_m.group(0), re.DOTALL)
                if len(tds) >= 5:
                    notice_no = clean_text(tds[1])
                    title = clean_text(tds[2])
                    dept = clean_text(tds[3])
                    dt = parse_date(clean_text(tds[4]))
                    if dt and dt < cutoff_archive:
                        continue
                    page_has_valid = True
                    if is_target_notice(title):
                        detail_url = (
                            "https://eminwon.seogwipo.go.kr/emwp/gov/mogaha/ntis/web/ofr/action/OfrAction.do"
                            f"?jndinm=OfrNotAncmtEJB&context=NTIS&method=selectOfrNotAncmt"
                            f"&methodnm=selectOfrNotAncmtRegst&homepage_pbs_yn=Y&subCheck=N&not_ancmt_mgt_no={mgt_no}"
                        )
                        add_item({
                            "agency": "서귀포시",
                            "tag": get_tag(title),
                            "noticeNo": notice_no,
                            "title": title,
                            "dept": dept,
                            "date": format_korean_date(dt, today_date),
                            "isoDate": (dt or today_date).strftime("%Y-%m-%d"),
                            "summary": f"{title} 관련 고시·공고입니다. (서귀포시청 관리번호: {mgt_no})",
                            "url": detail_url,
                        }, dt)
            if not page_has_valid:
                break
            time.sleep(0.4)
    except Exception as e:
        error_count += 1
        print(f"[WARN] 서귀포시청 조회 오류: {e}")

    # 최신순 정렬
    recent_items.sort(key=lambda x: x.get("isoDate", ""), reverse=True)
    archive_items.sort(key=lambda x: x.get("isoDate", ""), reverse=True)

    # 기존 briefing_data.js 읽기 및 병합
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_js_path = os.path.join(root_dir, "briefing_data.js")
    existing_data = {}
    if os.path.exists(data_js_path):
        with open(data_js_path, "r", encoding="utf-8-sig") as f:
            raw_js = f.read()
        start_brace = raw_js.find("{")
        end_brace = raw_js.rfind("}")
        if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
            json_part = raw_js[start_brace : end_brace + 1]
            try:
                existing_data = json.loads(json_part)
            except json.JSONDecodeError:
                existing_data = {}

    # 3개 이상 기관에서 오류 발생 시 기존 오늘 데이터 보존
    if error_count >= 3 and today_key in existing_data and len(existing_data[today_key].get("notices", [])) > len(recent_items):
        print(f"[WARN] 다수 기관 조회 오류({error_count}건)로 기존 {today_key} 데이터를 유지합니다.")
        return

    range_label = (
        f"{today_date.year}. {today_date.month:02d}. {today_date.day:02d}. "
        f"(최근 {args.days}일: {cutoff_recent.month:02d}. {cutoff_recent.day:02d}. ~ {today_date.month:02d}. {today_date.day:02d}.)"
    )
    updated_at_str = now.strftime("%Y. %m. %d. %H:%M")
    existing_data[today_key] = {
        "label": range_label,
        "updatedAt": updated_at_str,
        "notices": recent_items,
        "archiveNotices": archive_items,
    }

    with open(data_js_path, "w", encoding="utf-8") as f:
        f.write("window.JEJU_BRIEFING_DATA = ")
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    print(f"[OK] {today_key} 업데이트 완료: 최근 {args.days}일 {len(recent_items)}건, 1개월 누적 {len(archive_items)}건 저장 (오류 {error_count}건)")


if __name__ == "__main__":
    main()
