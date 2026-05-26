"""Script to calculate coverages and update README.md shields."""

import json
import re
import sys
from interrogate.coverage import InterrogateCoverage


def update_readme():
    print("Calculating Doc Coverage...")
    cov = InterrogateCoverage(paths=["src"])
    results = cov.get_coverage()
    doc_perc = results.perc_covered

    print("Calculating Test Coverage...")
    try:
        with open("coverage.json", "r") as f:
            cov_data = json.load(f)
        test_perc = cov_data["totals"]["percent_covered"]
    except Exception as e:
        print(f"Failed to read coverage.json: {e}")
        print("Did you forget to run pytest --cov=c_linter --cov-report=json ?")
        sys.exit(1)

    print(f"Doc Coverage: {doc_perc:.1f}%")
    print(f"Test Coverage: {test_perc:.1f}%")

    with open("README.md", "r", encoding="utf-8") as f:
        content = f.read()

    doc_color = (
        "brightgreen" if doc_perc >= 100 else "yellow" if doc_perc >= 80 else "red"
    )
    test_color = (
        "brightgreen" if test_perc >= 100 else "yellow" if test_perc >= 80 else "red"
    )

    doc_badge = f"![Doc Coverage](https://img.shields.io/badge/Doc_Coverage-{doc_perc:.0f}%25-{doc_color}.svg)"
    test_badge = f"![Coverage](https://img.shields.io/badge/Coverage-{test_perc:.0f}%25-{test_color}.svg)"

    # Replace existing badges
    content = re.sub(r"!\[Doc Coverage\]\(.*?\)", doc_badge, content)
    content = re.sub(r"!\[Coverage\]\(.*?\)", test_badge, content)

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(content)

    print("Successfully updated README.md shields.")


if __name__ == "__main__":
    update_readme()
