"""
Turn the test results into the report posted on a pull request.

    python .github/scripts/test-report.py results.json report.md [migrations-outcome]

Tests are grouped by what they cover, in plain words, so the report reads as
"which parts of the library still work" rather than a list of class names.
A test class not listed below still appears, under its own name, so a new
area is never silently left out of the count.
"""

import json
import re
import sys
from collections import OrderedDict

# Feature area -> the test classes that cover it. Order is the report's order.
AREAS = OrderedDict([
    ('Library pages (home, listings, a work)', [
        'QasidaViewsTest', 'LandingPageTest', 'ClickableCardTest', 'StaticPageTest',
        'RenderedOutputTest', 'TemplateCommentTest', 'PageDetailFixTest']),
    ('Search and filters', [
        'LiveSearchTest', 'TagFilterTest', 'AuthorFilterTest', 'SearchTextTest',
        'TagCategoryTest']),
    ('Poets, dedications, collections', [
        'PoetRecordTest', 'DedicationTest', 'DedicationBrowsingTest']),
    ('Verse layout (original, transliteration, translation)', [
        'LayerPairingTest', 'IndependentLayerAlignmentTest', 'VerseMarkerTest',
        'NativeScriptTest']),
    ('PDF downloads', ['ScansInThePdfTest']),
    ('Works, the review gate, data safety', [
        'QasidaModelTest', 'ReviewGateTest', 'DataSafetyTest']),
    ('Sign up, sign in, sessions', [
        'RegistrationTest', 'SignInTest', 'PasswordResetTest', 'EmailVerificationTest']),
    ("A reader's own library and settings", [
        'FavouriteTest', 'ReadingHistoryTest', 'AccountSettingsTest', 'DeleteAccountTest']),
    ('Corrections, contributions, contact', [
        'SignedInSuggestionTest', 'SuggestionFieldsTest', 'SuggestionNotificationTest',
        'SuggestionDecidedOnceTest', 'ContributionTest', 'ContributionReviewTest',
        'ContactFormTest']),
    ('Admin screens', [
        'AdminUserManagementTest', 'AdminListingTest', 'AdminWidgetTest',
        'TagAxisWidgetTest', 'MissingDetailFilterTest', 'PoetEditingTest',
        'AdminActionPermissionTest', 'AddToCollectionTest', 'QasidaEditFormTest']),
    ('Duplicate detection and comparison', [
        'DuplicateScanTest', 'DuplicateComparisonTest', 'DuplicateRulingTest']),
    ('Crawlers (fetching, extraction, safety)', [
        'CrawlerSafetyTest', 'PoliteFetchTest', 'ExtractionLineTest']),
    ('Security and abuse limits', [
        'AbuseLimitTest', 'ClientAddressTest', 'ReferrerPolicyTest']),
    ('Offline reading', ['OfflineViewerTest']),
    ('Speed (queries per page)', ['QueryCountTest']),
    ('Search engines and sharing', ['DiscoverabilityTest']),
    ('Accessibility', ['FormAccessibilityTest']),
    ('Email delivery settings', ['MailConfigTest']),
])
AREA_OF = {cls: area for area, classes in AREAS.items() for cls in classes}

TITLE = '## 📜 Qasida Library — test report'


def areas(count):
    return f"{count} feature area{'s' if count != 1 else ''}"


def duration(seconds):
    seconds = int(round(seconds or 0))
    return f'{seconds // 60}m {seconds % 60}s' if seconds >= 60 else f'{seconds}s'


def readable(test):
    """The test's own first docstring line, else its name in words."""
    if test.get('description'):
        return test['description'].rstrip('.')
    name = re.sub(r'^test_', '', test['name']).replace('_', ' ')
    return name[:1].upper() + name[1:]


def last_line(detail):
    lines = [line for line in (detail or '').strip().splitlines() if line.strip()]
    return lines[-1].strip()[:200] if lines else ''


def build(results, migrations_ok):
    tests = results['tests']
    rows = OrderedDict((area, {'passed': 0, 'failed': 0, 'skipped': 0}) for area in AREAS)
    for test in tests:
        area = AREA_OF.get(test['class'], f"Other: {test['class']}")
        rows.setdefault(area, {'passed': 0, 'failed': 0, 'skipped': 0})
        rows[area][test['outcome']] += 1
    rows = OrderedDict((area, counts) for area, counts in rows.items() if sum(counts.values()))

    totals = {key: sum(row[key] for row in rows.values()) for key in ('passed', 'failed', 'skipped')}
    failed = [test for test in tests if test['outcome'] == 'failed']
    took = duration(results.get('seconds'))

    if failed:
        headline = (f"> ❌ **{totals['failed']} check{'s' if totals['failed'] != 1 else ''} failed**, "
                    f"{totals['passed']} passed, across {areas(len(rows))} in {took}.")
    elif not migrations_ok:
        headline = ('> ❌ **The models and the migrations disagree.** Run `makemigrations` '
                    f"and commit the result. The {totals['passed']} checks themselves passed.")
    else:
        headline = (f"> ✅ **All good — {totals['passed']} checks passed** across "
                    f"{areas(len(rows))} in {took}.")

    out = [TITLE, '', headline, '',
           '| Feature area | Passed | Failed | Skipped | |', '|---|---:|---:|---:|:--|']
    for area, row in rows.items():
        mark = '❌' if row['failed'] else '✅'
        out.append(f"| {area} | {row['passed']} | {row['failed']} | {row['skipped']} | {mark} |")
    out.append('| Models match the migrations | | | | '
               + ('✅ |' if migrations_ok else '❌ |'))
    total_mark = '❌' if (failed or not migrations_ok) else '✅'
    out.append(f"| **Total** | **{totals['passed']}** | **{totals['failed']}** | "
               f"**{totals['skipped']}** | {total_mark} |")

    if failed:
        out += ['', '### What failed', '']
        for test in failed:
            area = AREA_OF.get(test['class'], test['class'])
            out.append(f"- **{area}** — {readable(test)}  \n  `{test['id']}`"
                       + (f"  \n  {last_line(test['detail'])}" if test.get('detail') else ''))
        out += ['', '<details><summary>Tracebacks</summary>', '']
        for test in failed:
            out += [f"**{test['id']}**", '', '```', (test.get('detail') or '').strip(), '```', '']
        out.append('</details>')
    return '\n'.join(out) + '\n'


def main():
    source, target = sys.argv[1], sys.argv[2]
    migrations_ok = (sys.argv[3] if len(sys.argv) > 3 else 'success') == 'success'
    try:
        with open(source, encoding='utf-8') as handle:
            results = json.load(handle)
        report = build(results, migrations_ok)
    except (OSError, ValueError, KeyError) as error:
        report = (f"{TITLE}\n\n> ❌ **The tests did not produce results** ({error.__class__.__name__}). "
                  f"They probably failed to start; the job log says why.\n")
    with open(target, 'w', encoding='utf-8') as handle:
        handle.write(report)


if __name__ == '__main__':
    main()
