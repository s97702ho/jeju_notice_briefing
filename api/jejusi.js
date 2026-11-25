module.exports = async function handler(req, res) {
  const page = req.query.page || "1";
  const targetUrl = `https://www.jejusi.go.kr/information/intro/notice.do?currentPageNo=${encodeURIComponent(page)}`;

  try {
    const response = await fetch(targetUrl, {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.jejusi.go.kr/information/intro/notice.do"
      }
    });

    const html = await response.text();
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.status(response.status).send(html);
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
}
