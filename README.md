# WPBrücke · for WordPress

WPBrücke lets Hermes run one WordPress site or many. Hermes works on your WordPress sites as a
WordPress user you create for it. Its role decides what Hermes may do: read, write drafts, or
publish. WPBrücke refuses a user with wider rights than the level you confirmed.

Hermes writes blog posts and page changes, proves every change in a real browser at three screen
widths, checks SEO, and puts each result on a review desk. It never sends a newsletter.

It works on plain WordPress over the core REST API and an application password. No paid plugin or
licence is needed. An optional companion WordPress plugin (the WPBrücke connector) adds a read-only
role, a drafts-with-images role, draft screenshots, SEO fields and a review screen. Theme depth
ships as optional packs; the first is the GeneratePress pack (GeneratePress and GenerateBlocks from
wordpress.org).

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

## Setup: connect a site

The WPBrücke page in Hermes Desktop walks you through this (**Connect a site**). It names the first
free group, Site 1 to Site 16.

1. **In WordPress, create a user for Hermes**, named `hermes`, with the role for the level you want
   (table below). Use a user of its own, never your own login: the role is the limit, and a user
   of its own can be changed or deleted without touching yours.
2. **Signed in as that user**: wp-admin → Users → Profile → **Application Passwords** → name it
   `Hermes` → **Add New Application Password**, and copy it.
3. **In Hermes Desktop**: on the WPBrücke page, **Open the settings**, then fill **Site N ·
   address**, **Site N · WordPress user** and **Site N · application password** and **Save**.
4. **Back on the WPBrücke page** the connection is checked. It shows what WordPress lets this user
   do ("Connected as hermes at example.com. WordPress lets this user write drafts with images.
   Hermes will do exactly that."). Click **Confirm**. Apart from the connection check, nothing uses
   the password before you confirm.

When the check fails, the page names the cause and the next step: a wrong user or password, a web
server that drops the login header (the `.htaccess` line to add), application passwords turned
off, Plain permalinks, an address that redirects, an administrator user.

A public site without a password is added on the WPBrücke page with its address only, or by asking
Hermes (`wp_sites action=add`). Hermes then reads only what the public sees.

**Remove a site:** on the WPBrücke page, **Remove** revokes the application password in WordPress
(optional), empties Site N's address and user, and deletes the site file. If emptying the fields
fails, the page asks you to empty Site N · address and Site N · WordPress user in the settings.

Optional extras:

- `hermes wpbruecke doctor` (or ask "run wp_doctor") names the user behind each key, its level and
  which features stay dark and why.
- The connector: `hermes wpbruecke connector-zip --out ~/Downloads`, then wp-admin → Plugins → Add
  New → **Upload Plugin** → **Activate**. Needed for the Read level and for the drafter role.
- The watcher: `hermes wpbruecke watch-setup` prints a script and the `hermes cron create` line for a
  no-agent job that reports only changes.

## Access levels

| Level | WordPress role of the Hermes user | What Hermes may do |
| --- | --- | --- |
| Read | **WPBrücke reader** (connector 0.2.1 or newer) | Read posts, pages and drafts, run proof and SEO checks. Every write is refused by WordPress itself, over REST and XML-RPC; the role stores only read rights. Give the user this role alone: a reader with a second role is refused |
| Drafts | **Contributor** (no images) or **WPBrücke drafter** (with images, connector) | Write drafts, staged copies of live posts and SEO fields. Publishing opens WordPress, where your own login publishes |
| Publish | **Author** (own posts) or **Editor** (all posts and pages) | All of the above, plus publish, schedule and apply staged copies, without asking |
| Refused | **Administrator**, or any user who can manage options, users, plugins or themes | Nothing. Use a user with one of the roles above |

A Subscriber can only read what the public sees.

- **You confirm the level once.** WPBrücke reads the user's role again before acting (at most 60 s
  old for reads, 5 s for writes) and on every watcher tick. More rights than you confirmed stop the
  site until you confirm again on the page; fewer rights lower the level on their own.
- **Within the level there is no approval card.** At the Publish level Hermes publishes when you ask
  it to, and the desk's Publish, Schedule and Apply buttons act directly.
- **Scheduled runs write drafts.** A recurring-draft automation you wrote in a site file writes a
  draft on that one site. It publishes only when the automation says `publish: true` and the level
  is Publish, and then only the post that same run created. Every other unattended write (cron, webhooks, single-query runs) is refused before any
  request.

**The honest limit.** Only the WordPress role holds against everything. Hermes stores the
application password in the profile's `.env`, so anything with shell access on the Hermes host (an
agent's terminal tool included) can read it and use it with the full rights of the role. WPBrücke's
own checks stop Hermes' tools from going past the confirmed level; they cannot stop a shell. Pick the
narrowest role that does the job.

## Update from 0.1.0

Your passwords stay where they are (`WPBRUECKE_KEY1` … `WPBRUECKE_KEY12` are Site 1 to Site 12's
passwords now). On the first load, each 0.1.0 site file with a key fills its group's address and
user. Its level is not carried over: the WPBrücke page checks the connection and asks you to
confirm it, and apart from the connection check nothing uses the password until you do. The 0.1.0 settings `publishing`,
"never publish, never send" and `agent_writes` are gone; the role replaces them. An administrator key
from 0.1.0 is refused: create a user with one of the roles above. Update the connector to 0.2.1 for
the Read level. Connector 0.2.0 is not enough: its reader role stored edit rights in WordPress, so a
reader on 0.2.0 is refused until you upload 0.2.1, which removes them.

## Settings

| Setting | Default | What it does |
| --- | --- | --- |
| `site1_url` … `site16_url`, `site1_user` … `site16_user` | empty | Site N's address and the WordPress user Hermes works as (login or email); an empty address means the group is free |
| `site1_password` … `site16_password` (secret, `WPBRUECKE_KEY1` … `WPBRUECKE_KEY16`) | empty | Site N's application password. WPBrücke sends it only to the address it was last checked against, over https |
| `chromium_path` | empty (Hermes' Chromium) | Another Chromium for proofs |
| `digest` | on | The watcher also reports updates, slow sites and items waiting; off: outages only |
| `psi_key` (secret, `WPBRUECKE_PSI_KEY`) | empty | PageSpeed Insights key; without it speed is measured locally and labelled "local, not Google" |
| `gsc_key` (secret, `WPBRUECKE_GSC_KEY`) | empty | Search Console service account JSON (or its base64); see below |

Each site has a readable YAML file (`sites/<id>.yaml` in the plugin's data folder): its address,
login and group (from the settings), the confirmed access level, proof widths, a short memory (tone,
languages, pages not to touch), recurring-draft automations, an optional Search Console property and
an optional Coolify app for the Coolifybrücke link. Every save keeps a version with author and diff.
Hermes cannot change a connected site's address, user, group or access level; changes to a connect IP,
proof hosts or automations ask first and are refused unattended.

The WPBrücke page reads its passwords fresh on each request: in the profile's plugin host process it
asks Hermes' secret scope builder (`agent.secret_scope.build_profile_secret_scope`, not a documented
plugin API) for the `WPBRUECKE_*` keys, at most every two seconds, so a password saved in the settings
shows on the page without a restart.

## What it does

| Surface | What it does |
| --- | --- |
| **WPBrücke page** (Desktop) | One site: its desk, SEO, health and settings. Many sites: the fleet, then each site. Sidebar row "WPBrücke · N" with the items waiting for you (show switch and position on the settings page), three palette commands |
| **Review desk** | Every draft and staged change with screenshots at 1440, 768 and 390 px, the proof's verdicts and SEO notes. Mark reviewed, ask for changes (a prefilled chat); at the Publish level Publish, Schedule and Apply, below it open WordPress |
| **Proof** | A headless browser renders the page: console errors, horizontal overflow, broken same-site links, image alt text, contrast, local load time, the pixel difference against the live page. Each check answers match, differs or not proven with the reason |
| **SEO** | Post check (title, description, headings, internal links, alt text, slug, readability in English and German), site check (indexing, sitemap, robots, home description, canonical, mobile, speed), fixes staged as drafts |
| **Staged changes** | A change to a published post is a new draft copy that points to its source. The live post stays untouched until the copy is applied: by Hermes or the desk at the Publish level, otherwise on the connector's review screen |
| **In chat** | `@wp:<site>/<post id>` adds a post to your message, and a draft card shows what Hermes wrote |
| **Watchers** | `hermes wpbruecke tick` reads every site and prints only what changed: down, back, slow, new updates, items waiting. A clean tick prints nothing |
| **Recurring drafts** | An automation you write in a site file ("every Monday a draft from my notes") may write drafts on that one site from a cron job named `wpbruecke:<site>:<automation id>`, and publish only with `publish: true` at the Publish level, and only the post that run created; every other unattended write is refused |
| **Build run** (GeneratePress pack) | Brief, brand, sitemap, pages as GenerateBlocks (or core block) drafts, header and footer, proof, client handoff. Each step names who acts; every gate is your click |
| **Connector** (WordPress plugin) | Reader role (read-only, enforced by WordPress), drafter role, signed preview links so drafts can be screenshotted, the SEO description printed in the page head, update counts, the review screen where your own click publishes, schedules or applies a staged copy |
| **Tools** | `wp_doctor`, `wp_sites`, `wp_content`, `wp_draft`, `wp_desk`, `wp_proof`, `wp_seo`, `wp_build` |

## Your sites, your click

- **The role is the limit.** See Access levels above.
- **Prompt injection.** Post content and comments are data in tool results. A write goes only to
  the site named in the call.
- **"Discourage search engines"** is never changed by Hermes; the SEO check links to the setting.
- **Passwords travel only to their own site.** A password is sent only to the address it was last
  checked against, over https, never across a redirect. When Site N's address changes to another
  host, its old password is not sent anywhere until you paste the new site's password.

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
  access there; only the WordPress role limits what that password can do (Access levels, the
  honest limit).
- **WordPress side:** the connector adds two roles (`wpbruecke_reader`, `wpbruecke_drafter`), post meta
  (`_wpbruecke_description`, `_wpbruecke_title`, `_wpbruecke_source`), options for its preview
  secret and a staged GeneratePress brand, and an admin page. The reader role stores only read
  rights; the connector adds the edit rights that show drafts only while it serves a REST read.
  Deactivating it removes the reader role (on every site of a network), so a reader user is left
  with no rights at all; deleting it removes both roles and its
  options.
- **Telemetry:** none.

## Development

Tests live in `tests/`. Run them with `python -m pytest tests` from the plugin folder, with Hermes'
source on `PYTHONPATH`; `hermes plugins validate` and `hermes plugins doctor` check the install.

## License

MIT, see `LICENSE`. The connector WordPress plugin is MIT too (GPL-compatible).
