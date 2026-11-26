# How WPBrücke is tested

WPBrücke is proven in four places. Each one catches a different kind of fault, and none of them
replaces the others. This page says what each place proves, how to run it, and what none of them
proves yet.

## Unit and behaviour tests

Run them from the plugin folder, with Hermes' source on `PYTHONPATH` and an empty Hermes home:

```bash
PYTHONPATH=<hermes source> HERMES_HOME=$(mktemp -d) python -m pytest tests -q -p no:cacheprovider --import-mode=importlib
```

They run in seconds against fake WordPress answers and assert what a user of a tool sees: the
answer, the refusal and its code, and the requests that were or were not sent. Among them:

- The access model: each WordPress role maps to its level, a wider role stops the site, and an
  unattended run is refused before any request.
- The builder guard and the packs: one fixture per page builder, unknown blocks kept, pack ids
  checked, the `packs` field guarded.
- The Elementor pack (`test_v04_elementor_model.py`, `test_v04_elementor_pack.py`): the tree and its
  editable marks, each refusal (`elementor_read_only`, `elementor_structure`, `elementor_dynamic`,
  `elementor_untested`, `elementor_scope`, `drafter_page`), the read-back hash, and design tokens
  from Elementor's globals. One fixture is the tree a real Elementor 4.4.2 answered in the
  Playground harness (`fixtures/elementor/harness-v3-page.json`).
- The write log and the scrubber (`test_v04_log.py`, `test_v04_redact.py`): no password,
  application password or bearer token survives, rotation at 5 MB or 90 days, whole lines from two
  processes, and every log query filter.
- The abilities lane and `wp_pack` (`test_v05_abilities.py`, `test_v05_pack_tool.py`): discovery
  and the method each ability's annotations ask for, on answers a real harness recorded
  (`fixtures/abilities/`); an ability no pack declares refused with no request; every `wp_pack`
  refusal (`unattended`, `person_only`, the level, `needs_permission`), and a card on every call.
- The WooCommerce pack (`test_v05_woo.py`): the shop roles in the access model, the description laid
  out in the site's design, each route refusal, the publish ops refused at the Drafts level before
  any request, the read-back after a publish, and the connector's shop route and publish guard in the
  PHP smoke when PHP 8 is on the host.
- The FluentCRM pack (`test_v05_fluentcrm.py`): the screen's boxes read through the permissions
  route, the screen named while it was never saved, the body built as block markup, test mails to the
  saved address only, the card on every schedule, and the desk item of a campaign.
- Version equality: the plugin header, `WPBRUECKE_CONNECTOR_VERSION`, `connector_zip.py`
  `CONNECTOR_VERSION` and `desktop/plugin.js` `CONNECTOR_NEEDED` stay equal, and the connector this
  plugin ships satisfies every pack, so `wp_doctor` names no connector update for it
  (`test_v05_release.py`).

## The page harness

`tests/desktop_page_harness.mjs` renders `desktop/plugin.js` in Chromium with a fake Desktop SDK
and checks each page state: the site cards, the badges, the connector buttons, and the Activity
disclosure closed until opened. `tests/test_desktop_page.py` runs it and skips without Playwright
(set `PLAYWRIGHT_FROM` to a folder whose `node_modules` holds it). `tests/test_proof_chromium.py`
runs the proof runner in a real Chromium against a local server and skips without Chromium.

## The opt-in Playground pairs

Four test files boot a real WordPress in WordPress Playground (Node, no Docker) and drive the
plugin's own handlers against it. They skip unless you opt in, because a boot takes one to six
minutes:

```bash
WPB_HARNESS=1 PYTHONPATH=<hermes source> python -m pytest tests/test_harness_connector.py tests/test_harness_elementor.py tests/test_harness_woo.py tests/test_harness_fluentcrm.py -v -p no:cacheprovider --import-mode=importlib
```

The Playground harness lives in the development repository, not in the published plugin. The tests
look for it next to the plugin in the repository layout; set `WPB_HARNESS_DIR` to point elsewhere.
Without it they skip and say so. They also need `node`, `bash` and `flock`. Only one Playground runs
at a time: the harness waits for a shared lock and holds it until it stops.

| File | WordPress | What it proves |
| --- | --- | --- |
| `test_harness_connector.py` (the min pair) | 6.5 on PHP 7.4, the connector's declared minimum, no other plugin | The connector activates and `info` answers its own version. The `wpbruecke_builder` field answers in edit context only. The Elementor read route answers 409 `wpbruecke_no_elementor` without Elementor |
| `test_harness_elementor.py` (the full pair) | 7.1.5 on PHP 8.3 with Elementor 4.4.2, WooCommerce and FluentCRM | `wp_doctor` shows the Elementor pack detected, on and needing nothing. `wp_content get` answers the tree with editable marks. A heading and a text edit are staged, previewed through the signed link with the copy's own CSS, proved at three widths and applied by the agent: an anonymous visitor sees the change without a cache clear, the copy is in the trash, and the write log names both writes. A Canvas page keeps its template, and a person's **Apply copy** on the review screen reaches the live page. The refusals: `live_changed` after someone edited the live page, `drafter_page` and the drafter's apply, the reader on every POST route, and `kses_would_strip` for an Editor without `unfiltered_html`, with the embed left as it was |
| `test_harness_woo.py` (the full pair) | 7.1.5 on PHP 8.3 with WooCommerce 11.3.1 | The shop drafter lists products, writes a draft product whose description is laid out in the site's design with a featured image, proves it through the signed preview link, and is refused a publish by WPBrücke (`level_drafts`) and by the connector's guard on WooCommerce's own REST API. The shop publisher creates a live product, reads back `publish`, changes it and proves it at its permalink. Deactivating the connector removes both roles |
| `test_harness_fluentcrm.py` (the full pair; `WPB_FCRM_PRO=1` adds FluentCRM Pro) | 7.1.5 on PHP 8.3 with FluentCRM 3.3.5 (and Pro 3.3.1) | Until the screen is saved the pack runs nothing and names it. With the default boxes a draft in the site's design opens as blocks in FluentCRM's editor and its proof renders at 600 and 390 px. Every direct FluentCRM write and send path refuses the Hermes user after each route call. The test mail reaches the saved address only. Schedule is refused without its box, and with it shows a card every time. Deactivating the connector takes the grants back and keeps a second user's |

**The newest Elementor.** Set `ELEMENTOR=latest` (or a release number) to boot Elementor's
newest release from wordpress.org instead of the pin. On such a run a release outside
`ELEMENTOR_TESTED` (`packs/elementor/widgets.py`) counts as tested for that run only. A green run
is the evidence that widens the range in the next release; a red run leaves the range as it is,
and this page records what failed. Outside the range the pack keeps every Elementor page body
read-only and names the tested range.

| Date | Newest Elementor | Result | `ELEMENTOR_TESTED` |
| --- | --- | --- | --- |
| 2026-11-26 | 4.4.2 (the pinned release) | green, both pairs | 4.4.2 to 4.4.2, unchanged |

## The hands-on test of each release

Before a release ships, a person installs it in Hermes Desktop and follows a written test guide
against a real WordPress site: the Desktop page, the cards, the review desk, the **Download
connector** button, the connector upload, and the release's own features (an Elementor edit in
0.4; a draft product, a draft campaign and a test email in 0.5). Its first block is one read turn
and one staged write on a real test site, for the gap below.

## Known gaps

- **Real hosts.** Every automated live run uses a local Playground over plain http on 127.0.0.1.
  Faults that need a real host show first in the hands-on test: TLS, a page cache or proxy cache
  in front of WordPress, a CDN, a VPN route, and a web server that drops the `Authorization`
  header. That test's first block covers them with one read and one staged write.
- **Real mail.** The harness short-circuits `wp_mail`, so no FluentCRM test mail leaves it. A mail
  that a real host sends and a real inbox receives shows first in the hands-on test.
- **FluentCRM Pro.** The Pro pair needs the Pro zip in the harness's plugins folder (no licence key);
  without it only free FluentCRM runs.
- **Call-to-action.** The call-to-action widget comes with Elementor Pro. The harness runs the free
  Elementor, so call-to-action edits are tested on fixtures only.
- **Elementor releases.** The pack is tested on the releases `ELEMENTOR_TESTED` names. A new
  Elementor release keeps pages read-only until a run on it is green and a WPBrücke release widens
  the range.
- **A Playground artifact.** Playground's workers share files with a short lag. A file that was
  deleted and written again, such as an Elementor CSS file after a stage, can answer 500 from
  another worker for a moment. The harness helpers repeat an anonymous read up to three times. A
  500 that goes away on the next read is this artifact, not WordPress or the plugin.
