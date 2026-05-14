from teamrag.api.document import source_url_match_variants


def test_source_url_variants_with_and_without_slash():
    assert source_url_match_variants("https://example.com/page") == [
        "https://example.com/page",
        "https://example.com/page/",
    ]


def test_source_url_variants_trailing_slash_input():
    assert source_url_match_variants("https://example.com/page/") == [
        "https://example.com/page/",
        "https://example.com/page",
    ]
