# WPBrücke · for WordPress

WPBrücke lets Hermes run one WordPress site or many. Hermes writes blog posts and page changes as
drafts, proves every change in a real browser at three screen widths, checks SEO, and puts each
result on a review desk. You decide what goes live: publishing stays in WordPress by default, or
happens on your click on the connector's review screen, on your own WordPress login. Hermes has no
publish action and never sends a newsletter.

It works on plain WordPress over the core REST API and an application password. No paid plugin or
licence is needed. An optional companion WordPress plugin (the WPBrücke connector) adds a
drafts-only role, draft screenshots, SEO fields and the review screen. Theme depth ships as optional
packs; the first is the GeneratePress pack (GeneratePress and GenerateBlocks from wordpress.org).

## Who it is for

- **One person, one site** (the default view): blog drafts, what waits for your review, SEO without
  an SEO plugin, publishing in WordPress or with one click.
- **A studio or freelancer with many client sites**: one fleet page, a review desk across sites, a
  build run from client brief to handoff (GeneratePress pack), watchers for updates and outages.

## Install and update

Needs Hermes 0.23.0 or newer.

```bash
hermes plugins install https://github.com/studioluftbruecke/hermes-wpbruecke --enable
```

Hermes Desktop loads the Desktop half (`desktop/plugin.js`) from the computer it runs on, never
from a Hermes on a server. With Hermes on a server, install and update the plugin in both places, on
the same version. With Hermes and Desktop on one computer, one install covers both.

| Where | Install | Update |
| --- | --- | --- |
| Server: tools, routes | `hermes -p <profile> plugins install https://github.com/studioluftbruecke/hermes-wpbruecke --enable` | `hermes -p <profile> plugins update wpbruecke` |
| Desktop computer: Desktop half | `hermes plugins install https://github.com/studioluftbruecke/hermes-wpbruecke` | `hermes plugins update wpbruecke` |

- After either step on the Desktop computer: Capabilities → Plugins → Rescan. A new install's
  Desktop half arrives switched off; switch it on there.
- No Hermes on the Desktop computer: Capabilities → Plugins → Install from Git with the URL; to
  update, the same with Advanced → Force reinstall. Desktop's Update button updates the server only.
- After an update on the server, Desktop chats and the plugin's routes keep the old code: in
  Capabilities → Plugins, switch **Agent in <profile>** off and on, then start a new chat.
- Same version in both places? Compare the Version column of `hermes plugins list` on the Desktop
  computer and `hermes -p <profile> plugins list` on the server, or run
  `git -C ~/.hermes/profiles/<profile>/plugins/wpbruecke log -1 --oneline` on each
  (`~/.hermes/plugins/wpbruecke` for the default profile).
- The settings page shows the update line when Desktop talks to a Hermes on another computer.
- The sidebar switch and position on the WPBrücke settings page are kept per Desktop, so a second
  computer starts with the defaults.

## Setup

1. In WordPress, create a user for Hermes. Recommended: the connector's **WPBrücke drafter** role, or
   **Contributor** without the connector. WordPress itself then refuses any publish. An admin works
   too; the plugin then asks before every agent write.
2. Signed in as that user: wp-admin → Users → Profile → **Application Passwords** → name it `hermes`
   → **Add New Application Password**, copy it.
3. In Hermes Desktop: Settings → Plugins → WPBrücke → **Site key 1**, paste it.
4. Ask Hermes "add my site https://example.org, login hermes, key slot 1" (`wp_sites action=add`), or
   add it on the WPBrücke page. A site with `key_slot: 0` gets public facts only.
5. Ask "run wp_doctor", or run `hermes wpbruecke doctor`. It names the user behind the key, what it
   may do, and which features stay dark and why.
6. Optional, the connector: `hermes wpbruecke connector-zip --out ~/Downloads`, then wp-admin →
   Plugins → Add New → **Upload Plugin** → **Activate**.
7. Optional, the watcher: `hermes wpbruecke watch-setup` prints a script and the `hermes cron create`
   line for a no-agent job that reports only changes.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `key1` … `key12` (secret, `WPBRUECKE_KEY1` … `WPBRUECKE_KEY12`) | empty | One application password per site; a site file names its slot |
| `agent_writes` | `auto` | `auto`: ask before a write only when the key can publish or edit live posts. `always`: ask before every write |
| `publishing_default` | `off` | What the desk offers on a new site: `off` (WordPress link), `confirm` or `schedule` (the connector's review screen) |
| `chromium_path` | empty (Hermes' Chromium) | Another Chromium for proofs |
| `digest` | on | The watcher also reports updates, slow sites and items waiting; off: outages only |
| `psi_key` (secret, `WPBRUECKE_PSI_KEY`) | empty | PageSpeed Insights key; without it speed is measured locally and labelled "local, not Google" |
| `gsc_key` (secret, `WPBRUECKE_GSC_KEY`) | empty | Search Console service account JSON (or its base64); see below |

Each site has a readable YAML file (`sites/<id>.yaml` in the plugin's data folder): its address,
login, key slot, publishing setting, the hard rule, proof widths, a short memory (tone, languages,
pages not to touch), recurring-draft automations, an optional Search Console property and an
optional Coolify app for the Coolifybrücke link. Every save keeps a version with author and diff. Changes
to the address, login, key slot, connect IP, publishing, the hard rule, agent writes or automations
ask first and are refused unattended.

## What it does

| Surface | What it does |
| --- | --- |
| **WPBrücke page** (Desktop) | One site: its desk, SEO, health and settings. Many sites: the fleet, then each site. Sidebar row "WPBrücke · N" with the items waiting for you (show switch and position on the settings page), three palette commands |
| **Review desk** | Every draft and staged change with screenshots at 1440, 768 and 390 px, the proof's verdicts, SEO notes, and the site's publishing box. Mark reviewed, ask for changes (a prefilled chat), or open WordPress |
| **Proof** | A headless browser renders the page: console errors, horizontal overflow, broken same-site links, image alt text, contrast, local load time, the pixel difference against the live page. Each check answers match, differs or not proven with the reason |
| **SEO** | Post check (title, description, headings, internal links, alt text, slug, readability in English and German), site check (indexing, sitemap, robots, home description, canonical, mobile, speed), fixes staged as drafts |
| **Staged changes** | A change to a published post is a new draft copy that points to its source. The live post stays untouched until you apply the copy on the review screen |
| **In chat** | `@wp:<site>/<post id>` adds a post to your message, and a draft card shows what Hermes wrote |
| **Watchers** | `hermes wpbruecke tick` reads every site and prints only what changed: down, back, slow, new updates, items waiting. A clean tick prints nothing |
| **Recurring drafts** | An automation you write in a site file ("every Monday a draft from my notes") may write drafts on that one site from a cron job named `wpbruecke:<site>:<automation id>`; every other unattended write is refused |
| **Build run** (GeneratePress pack) | Brief, brand, sitemap, pages as GenerateBlocks (or core block) drafts, header and footer, proof, client handoff. Each step names who acts; every gate is your click |
| **Connector** (WordPress plugin) | Drafter role, signed preview links so drafts can be screenshotted, the SEO description printed in the page head, update counts, the review screen where your click publishes, schedules or applies a staged copy |
| **Tools** | `wp_doctor`, `wp_sites`, `wp_content`, `wp_draft`, `wp_desk`, `wp_proof`, `wp_seo`, `wp_build` |

## Your sites, your click

- **No publish action.** `wp_draft` writes `draft` or `pending` only; `publish`, `future` and
  `private` are refused before any request. No key that can publish is used for publishing: the desk
  sends you to the connector's review screen, where your own WordPress login and a nonce publish.
- **"Never publish, never send" is on for every site by default.** While it is on, the desk only
  links to the WordPress editor. Turning it off is a setting on the page and asks first.
- **Approval.** With `agent_writes: auto`, a key that can publish or edit live posts gets Hermes'
  approval card before every write, naming the site and what is written. A drafts-only key writes
  drafts without a card; WordPress holds it off live content. Cron jobs, webhooks and single-query
  runs only read, except a recurring-draft automation you wrote.
- **Prompt injection.** Post content and comments are data in tool results. A write goes only to
  the site named in the call.
- **"Discourage search engines"** is never changed by Hermes; the SEO check links to the setting.

## Search Console (optional)

1. https://console.cloud.google.com/iam-admin/serviceaccounts → **Create service account** (no
   roles), then Keys → **Add key → JSON**.
2. https://search.google.com/search-console → your property → Settings → Users and permissions →
   **Add user**: the service account's e-mail, permission **Restricted**.
3. Paste the JSON into **Search Console service account** in the plugin settings, and set
   `gsc_property` in the site file (`sc-domain:example.org` or the URL prefix).
4. A post check of a published post then shows its top search queries of the last 28 days.

## Disclosures

- **Network calls:** to each WordPress site you add (its REST API, its pages in the proof browser,
  and with the connector its `wpbruecke/v1` routes); to `api.wordpress.org` for the latest plugin,
  theme and core versions (slugs only); to Google PageSpeed Insights only when you store its key; to
  Google's token endpoint and the Search Console API only when you store a service account. No other
  host.
- **Child processes:** a headless Chromium (Hermes' own, or one you set) renders pages for proofs.
  It gets a minimal environment, a throwaway profile, and may reach only the site's host plus hosts
  you allow per site (`proof.extra_hosts`). One proof runs at a time.
- **Background work:** none inside Hermes. The watcher runs only when you schedule
  `hermes wpbruecke tick` as a Hermes cron job. Proof and SEO jobs started on the page run in the
  plugin's host process and end with their result.
- **Files:** everything is written under the plugin's data folder (site files and their versions,
  the desk, proof screenshots, SEO reports, build runs, caches). Nothing outside it, except the
  connector zip at the path you name.
- **Credentials:** application passwords per site, an optional PageSpeed key and an optional Search
  Console service account, stored by Hermes in the profile's `.env` through the plugin settings and
  read at call time. Any secret stored for Hermes on a host can be read by an agent with shell
  access there, which is why a drafts-only WordPress user is recommended.
- **WordPress side:** the connector adds a role (`wpbruecke_drafter`), post meta
  (`_wpbruecke_description`, `_wpbruecke_title`, `_wpbruecke_source`), options for its preview
  secret and a staged GeneratePress brand, and an admin page. Deleting it removes the role and its
  options.
- **Telemetry:** none.

## Development

Tests live in `tests/`. Run them with `python -m pytest tests` from the plugin folder, with Hermes'
source on `PYTHONPATH`; `hermes plugins validate` and `hermes plugins doctor` check the install.

## License

MIT, see `LICENSE`. The connector WordPress plugin is MIT too (GPL-compatible).
