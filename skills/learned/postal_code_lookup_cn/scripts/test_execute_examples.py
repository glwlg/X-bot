import unittest

from execute import _detect_query_type, _parse_candidates_from_html


class TestPostalLookupParsing(unittest.TestCase):
    def test_detect_query_type(self):
        self.assertEqual(_detect_query_type("100080"), "zipcode")
        self.assertEqual(_detect_query_type("北京市海淀区中关村"), "address")

    def test_parse_candidates_from_html_table(self):
        html = """
        <html><body>
        <table>
            <tr><th>地区</th><th>邮编</th></tr>
            <tr><td>北京市 海淀区 中关村</td><td>100080</td></tr>
            <tr><td>北京市 海淀区 学院路</td><td>100083</td></tr>
        </table>
        </body></html>
        """
        rs = _parse_candidates_from_html(html)
        self.assertTrue(any(x["zipcode"] == "100080" for x in rs))
        self.assertTrue(any("海淀区" in x["region"] for x in rs))

    def test_parse_candidates_from_plain_text(self):
        html = "<html><body>江苏省 无锡市 滨湖区 214000 相关信息</body></html>"
        rs = _parse_candidates_from_html(html)
        self.assertTrue(any(x["zipcode"] == "214000" for x in rs))


if __name__ == "__main__":
    unittest.main()
