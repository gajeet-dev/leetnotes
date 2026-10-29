import logging
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from problems.links import links
from problems.models import Problem

logger = logging.getLogger(__name__)


def build_link_lookup(items):
    """Map each collected text to its source URL."""
    return {
        item["text"].strip(): item["href"]
        for item in items
        if item.get("text") and item.get("href")
    }


def fetch_html(url):
    """Fetch HTML from the source URL."""
    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    response.raise_for_status()
    return response.text


def parse_html(html):
    """Parse HTML using BeautifulSoup."""
    return BeautifulSoup(html, "html.parser")


def extract_flowchart_href(html, page_url):
    """Extract the first anchor's href inside #flowchart."""
    soup = parse_html(html)

    flowchart = soup.find(id="flowchart")
    if flowchart is None:
        return None

    anchor = flowchart.find("a")
    if anchor is None:
        return None

    href = anchor.get("href")
    if not href:
        return None

    # Handle relative URLs.
    return urljoin(page_url, href)


def get_problem_text(problem):
    """Build the text used to match a Problem to a collected link."""
    return f"{problem.leetcode_id}. {problem.title}".strip()


def update_problem_flowchart(problem, link_lookup, session):
    """Fetch and save the flowchart URL for one Problem."""
    problem_text = get_problem_text(problem)
    source_url = link_lookup.get(problem_text)

    if not source_url:
        return "unmatched"

    try:
        response = session.get(
            source_url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()

        flowchart_url = extract_flowchart_href(
            response.text,
            response.url,
        )

        if not flowchart_url:
            return "missing_flowchart"

        if problem.flowchart != flowchart_url:
            problem.flowchart = flowchart_url
            problem.save(update_fields=["flowchart"])

        return "updated"

    except requests.RequestException:
        logger.exception(
            "Failed to fetch flowchart for Problem %s (%s)",
            problem.pk,
            problem_text,
        )
        return "error"


def update_all_problems(items):
    """Process all matching Problem records and report statistics."""
    link_lookup = build_link_lookup(items)

    stats = {
        "updated": 0,
        "unmatched": 0,
        "missing_flowchart": 0,
        "error": 0,
    }

    with requests.Session() as session:
        problems = Problem.objects.all().iterator()

        for problem in problems:
            result = update_problem_flowchart(
                problem,
                link_lookup,
                session,
            )
            stats[result] += 1

            print(f"[{result}] {get_problem_text(problem)}")

    return stats


class Command(BaseCommand):
    help = "Fetch and save flowchart URLs for matching Problem records."

    def handle(self, *args, **options):
        stats = update_all_problems(links)

        self.stdout.write("\nFlowchart update summary:")

        for status, count in stats.items():
            self.stdout.write(f"  {status}: {count}")

        self.stdout.write(self.style.SUCCESS("Processing completed."))
