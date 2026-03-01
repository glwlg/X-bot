from manager.integrations.github_client import parse_issue_reference, parse_repo_slug


def test_parse_repo_slug_supports_https_and_ssh():
    owner_https, repo_https = parse_repo_slug("https://github.com/acme/project.git")
    assert owner_https == "acme"
    assert repo_https == "project"

    owner_ssh, repo_ssh = parse_repo_slug("git@github.com:acme/project.git")
    assert owner_ssh == "acme"
    assert repo_ssh == "project"


def test_parse_issue_reference_supports_url_short_and_number():
    ref_url = parse_issue_reference("https://github.com/acme/project/issues/123")
    assert ref_url.owner == "acme"
    assert ref_url.repo == "project"
    assert ref_url.number == 123

    ref_short = parse_issue_reference("acme/project#77")
    assert ref_short.owner == "acme"
    assert ref_short.repo == "project"
    assert ref_short.number == 77

    ref_num = parse_issue_reference("42", default_owner="acme", default_repo="project")
    assert ref_num.owner == "acme"
    assert ref_num.repo == "project"
    assert ref_num.number == 42
