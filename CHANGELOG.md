# Changelog

## 0.5.0

Plugin packs: Hermes drafts WooCommerce products and FluentCRM newsletters through a new tool,
`wp_pack`, inside the site's level. Update both halves. Update the WordPress connector to 0.5.0 too:
both packs need its new roles, routes and screen, and the page offers it as a download.

- **`wp_pack` and the abilities lane.** One tool for plugin packs: `list`, `describe` and `run`. A pack
  declares its ops, and each op runs one ability of the plugin (through WordPress' Abilities API, 6.9
  or newer) or one connector route. Only declared ops run, and only on a site whose site file switches
  the pack on (`packs: {woocommerce: true}`, `packs: {fluentcrm: true}`); every other ability is
  refused before any request, and `wp_doctor` lists the abilities it sees but does not enable. A run is
  refused in scheduled and one-shot runs (`list` and `describe` still work), below the op's level
  (`level_read`, `level_drafts`), and when the site does not report the permission the op needs
  (`needs_permission`, with the wp-admin screen that grants it). An op that asks for a card asks on
  every call; "allow for this session" never covers the next one. Every write and refusal lands in
  the write log.
- **WooCommerce pack.** Lists products, writes draft products (name, description, prices, categories,
  featured image) and, at the Publish level, creates and changes live products and reads each one back
  (`changed` lists the fields that moved). A description given as words (`body`) is laid out in the
  site's colours and heading font. `prove-product` renders the product page at three widths, a draft
  through the signed preview link. It never reads or changes orders or customers and never deletes a
  product.
- **Shop roles.** The connector adds **WPBrücke shop drafter** (Drafts level: the Contributor's rights,
  uploads and its own draft products, never `publish_products`) and **WPBrücke shop publisher**
  (Publish level: the Author's rights plus publishing its own products). The site's admin gives one in
  wp-admin → Users, and you confirm the new level on the WPBrücke page. WPBrücke counts
  `publish_products` as a publish right. WooCommerce's Shop Manager is refused.
- **Publish guard.** WooCommerce's own REST API lets a user who may edit its draft product publish it.
  The connector refuses that save for any user who may edit products but not publish them, so the
  shop drafter publishes neither through WPBrücke nor through WooCommerce's API.
- **FluentCRM pack, free and Pro.** Drafts campaigns built from sections in the site's colours and
  fonts (block markup FluentCRM's editor keeps), renders FluentCRM's own preview at 600 and 390 px as
  the proof, and sends test copies to the one address the site's admin saved. Each campaign gets a desk
  item that links to FluentCRM's campaign editor. Another person's draft asks with a card first. It
  never sends to a single contact, never pauses, unschedules or deletes a campaign, and never writes
  contacts, lists, tags or automations. Tested with FluentCRM 3.3.5 and FluentCRM Pro 3.3.1.
- **The FluentCRM screen.** wp-admin → Settings → WPBrücke FluentCRM: an administrator picks the Hermes
  user and ticks **View campaigns**, **Write draft campaigns**, **Send test emails** and **Schedule and
  send campaigns**. Until it is saved for the user, the pack does nothing and names the screen. While
  the last box is off, the user holds only FluentCRM's "Read emails" permission, so FluentCRM itself
  refuses it every send. Drafts, previews and test emails go through three connector routes that lend
  FluentCRM's "Write and send emails" permission for one fixed call each. Saving gives the Hermes user
  its own FluentCRM contact, so previews never show a customer's data. Previews and test emails render
  against that contact by name (recreated when someone deleted it; refused with nothing sent when it
  cannot be). Naming it needs FluentCRM's "Read contacts" permission, so those two calls borrow it too,
  for that one call. `wp_doctor` shows the boxes.
- **A card on every schedule.** Where the admin ticked **Schedule and send campaigns**,
  `schedule-campaign` shows a card with the campaign, the send time and FluentCRM's recipient count,
  every time, and never runs unattended. Every pack card says what it does in its first sentence
  (a chat may show only the first lines) and cuts titles, list names and subjects to 120 characters.
- **Honest read-back.** A product write lists the fields WooCommerce did not keep (`dropped`,
  `truncated`), so a silently dropped stock count or an over-long name no longer reads as done.
- **Connector 0.5.0:** the two shop roles and the publish guard, `POST /shop/product` for draft
  products, the FluentCRM screen and four FluentCRM routes, the shop roles removed and the FluentCRM
  permissions taken back on deactivation, everything removed on delete.
- **WordPress.org directory package.** The connector's folder is ready for the plugin directory: a
  directory readme, banners, icons, screenshots, a Live Preview blueprint, and Plugin Check's "Plugin
  repo" checks without errors. A workflow, `.github/workflows/wporg-connector.yml`, deploys it to
  WordPress.org when you click **Run workflow** (a dry run by default); it reads the version from the
  connector's header and stops when `readme.txt`'s `Stable tag` differs.
- **Passwords from 1Password or Bitwarden.** The README says how: Hermes' own secret sources fill
  `WPBRUECKE_KEY*`, and WPBrücke reads them through `get_secret` like any saved password.

## 0.4.0

Elementor and the write log: Hermes changes texts, links and images on Elementor pages through staged
copies, and answers "what happened with X" from a log of every write. Update both halves: the
Activity list is in the Desktop half. Update the WordPress connector to 0.4.0 too: the Elementor pack
needs its new routes, and the page offers it as a download.

- **Elementor pack.** Switch it on per site with `packs: {elementor: true}` in the site file, or with
  the switch on the site's Settings tab. Hermes then changes texts, links and images of existing
  heading, text editor, button, image, icon list and call-to-action widgets. It never adds, removes
  or moves an element and never creates an Elementor page. Every other widget and every V4 atomic
  element stays read-only. Tested on Elementor 4.4.2. On another Elementor release the pack keeps
  every Elementor page body read-only and names the tested range.
- **An Elementor edit, step by step.** `wp_content get` answers `content_format: "elementor"` with the
  element tree and, per element, the settings Hermes may change. `wp_draft stage` and `update` take
  `elementor_edits`. The connector copies the page with Elementor's own copy of its data (the page
  template and featured image included) and saves through Elementor's own document save, so
  revisions and Elementor's CSS stay right. `wp_proof` shows the copy with its own CSS through the
  signed preview link. At the Publish level `wp_draft apply` puts the layout on the live page and
  clears Elementor's caches for it. Below that level you click **Apply copy to the live post** on the connector's
  review screen, which runs the same apply.
- **Elementor refusals.** Apply is refused when someone edited the live page after the copy was
  staged (`live_changed`): stage it again from the live page. The drafter role edits only its own
  Elementor posts, never pages (`drafter_page`). A user without WordPress' `unfiltered_html` right
  (an Editor on a multisite network) is refused where WordPress would strip HTML from parts of the
  page the edit does not touch (`kses_would_strip`). A setting that comes from a dynamic tag, an
  element outside the scope, and an edit that would add or remove an element are refused too.
- **Design tokens from Elementor.** With the Elementor pack on, `wp_sites get` reads the colours and
  fonts from Elementor's global styles. Your `design:` overrides still win.
- **Write log.** Every write WPBrücke does or refuses is one line in a log per site, in the plugin's
  data folder: the time, the tool and action, the post's title at that moment, who asked (Hermes,
  you on the page, an automation) and the result. `wp_doctor action=log` searches it by post id,
  title word, tool, action, result and time, and Hermes reads it before it answers "what happened
  with X". The log keeps 5 MB or 90 days per site. Every log line and every tool result passes a
  scrubber that removes passwords, application passwords and tokens.
- **Activity on the site page.** A closed **Activity** section at the foot of the site page lists the
  last 50 log lines when you open it. It reads nothing until then and shows no badge or count.
- **Elementor's own role changes no longer stop a site.** Elementor 4.4 adds its own capabilities to
  the Editor role on the first wp-admin visit after an update. 0.3.0 then stopped every site whose
  Hermes user is an Editor until you confirmed again. 0.4.0 leaves Elementor's capabilities out of
  that check, and a confirmation 0.3.0 recorded still holds. Any other new capability still stops
  the site.
- **Pages with dynamic tags Elementor cannot resolve stay read-only.** A dynamic tag from a plugin
  that is not active (Elementor Pro removed, say) would be lost on save, also on elements the edit
  does not touch. Stage and save refuse such a page (`elementor_unknown_dynamic`) and name the
  elements, and the check after each save covers dynamic tags too.
- **Apply keeps your later changes.** Apply also refuses `live_changed` when the live page's SEO
  fields or Elementor's display settings changed after staging, instead of overwriting them.
- **Scrubber.** It also masks an application password written with other separators, a base64
  `user:password` token a WordPress error may echo, values under secret-named keys in lists and
  objects, and `pwd=` values. An edit value above 64 KB is refused, and an error inside Elementor's
  save answers `elementor_error` with nothing written.
- **Connector 0.4.0:** four Elementor routes (read the tree, stage a copy, save edits on a draft or a
  copy, apply a copy), the review screen's **Apply copy to the live post** for Elementor copies, and the reader role
  refused on each new write route.

## 0.3.0

Watch and guard: the watcher sees broken sites, and Hermes leaves page builder layouts alone. Update
both halves: the page's connector button and the new badges are in the Desktop half. Update the
WordPress connector to 0.3.0 too; the page now offers it as a download.

- **Update alerts by offer.** The watcher tracks each offer by its key (WordPress, a plugin, a theme)
  and announces it when its offered version is new. A plugin that the connector and wordpress.org
  both report counts once. The first tick of a site, and the first 0.3.0 tick on a site 0.2.x
  watched, prints one summary of what is pending. A failed read of api.wordpress.org prints nothing.
- **Plugin and theme updates need one more right.** WordPress shows plugin and theme update offers
  only to users with the `update_plugins` capability, which only administrators hold, and WPBrücke
  refuses administrators. So with the roles WPBrücke accepts, the watcher sees WordPress core
  updates only, as in 0.2.x. A site owner who wants plugin and theme offers gives the Hermes user
  `update_plugins` with a role editor plugin.
- **Broken sites.** The watcher reads the public home page. Any 5xx answer or WordPress' error page
  is broken: one line, again after 6 hours, then a line when it is back. WordPress' maintenance page
  (503 with Retry-After) is "updating" and counts as broken only when the next tick still sees it.
  "Down" now means no answer at all. A site's down, broken and back lines come before its update
  lines. `wp_doctor` has a health line, and the page shows **Broken** or **Updating** on the site.
- **Builder pages keep their layout.** `wp_content get` names the page builder of a post: the block
  editor, the classic editor, Elementor, Divi, WPBakery, Beaver Builder, Bricks, Oxygen or
  Breakdance. `wp_draft` refuses to change the body of a builder page (`builder_page`) before it
  sends anything; title, excerpt, SEO fields and the featured image still work. No builder pack
  exists yet, so every builder page body is read-only in 0.3.0. The page shows each builder on the
  site with `· no pack`.
- **Blocks of other plugins stay.** A change that would drop blocks WPBrücke does not know (Kadence,
  Spectra, ...) is refused (`unknown_blocks`) unless the call names them in the new `drop_blocks`
  argument.
- **Packs.** The GeneratePress pack is the first entry of a pack registry. A site file's `packs:`
  takes only known pack ids (`unknown_pack` names the known ones), and switching a pack on is your
  decision: Hermes shows a card, and a scheduled run is refused (`packs_guarded`). `wp_doctor` lists
  each pack per site: detected, on, and what it still needs.
- **Design tokens read live.** `wp_sites get` returns the site's colours, fonts and logo from the
  theme (GeneratePress through the connector, else the theme's global styles). A new `design:` block
  in the site file holds your overrides only, merged key by key and listed under `overridden`.
- **Download connector on the page.** Where the connector shows `not installed` or `older than
  needed`, Settings → Connections offers **Download connector** (the zip, saved by Desktop) and
  **Install in WordPress** (opens the site's Upload Plugin screen). A Desktop that cannot save files
  from Hermes shows the `hermes wpbruecke connector-zip` command instead.
- **Connector 0.3.0:** the REST field `wpbruecke_builder` on posts and pages (edit context only),
  which names the builder from its meta on the server. Beaver Builder, Bricks, Oxygen and Breakdance
  are named only through it. Plugin and connector share one version from now on.

## 0.2.1

From the first hands-on test of 0.2.0. Update both halves: the copy changes are in the Desktop half too.

- **Clearer setup copy.** The Connect a site guide, the Connections page and the Agent settings say
  that each WordPress site needs a dedicated user for Hermes, and that its role decides what Hermes
  can do. The WordPress steps read as menu paths (WordPress → Users → Add New User).
- **A stop names the step that restarts the site.** A site stopped by an administrator role, a user
  without a role or a revoked password no longer says "confirm again" in the tool refusal, the
  doctor, the page's waiting items or the watcher. It says what to fix (the role, or a new
  application password) and to click Check again, with `next.action: check`. A wider role still
  asks to confirm again.
- **The doctor no longer blames the password while a site is stopped or unconfirmed.** Its feature
  list gives the reason the password is held back instead of "the key did not authenticate", and it
  reports an installed connector as installed (it no longer reads the connector's info route
  without a login, which always answered 401).

## 0.2.0

Sites live in Hermes' own plugin settings, and the WordPress role of the user you create for Hermes
is the access level. Update both halves: the plugin where Hermes runs and the one Hermes Desktop
loads. Update the WordPress connector to 0.2.1 too if you use it (the Read level needs 0.2.1).

- **Sites in the settings.** Each site is a group of three fields: Site N · address, Site N ·
  WordPress user, Site N · application password, for up to 16 sites. The password fields keep the
  env names `WPBRUECKE_KEY1` … `WPBRUECKE_KEY16`, so the passwords of a 0.1.0 install stay.
- **Connect a site on the WPBrücke page.** A guide names the free group and the three steps (a
  `hermes` user in WordPress with the role you want, its application password, the settings). The
  page then checks the connection and names the cause and the next step when it fails: wrong user or
  password, a web server that drops the login header, application passwords off, Plain permalinks,
  an address that redirects, an administrator user, a host that changed.
- **Access levels from the WordPress role, confirmed by you.** Read (the connector's new WPBrücke
  reader role), Drafts (Contributor, or the WPBrücke drafter with images), Publish (Author or
  Editor). Administrators are refused, and so is a reader with a second role. Apart from the
  connection check, nothing uses the password before you confirm.
- **A wider role stops the site.** The role is read again before acting and on every watcher tick.
  More rights than confirmed stop the site until you confirm again; fewer rights lower the level on
  their own.
- **Publishing at the Publish level.** `wp_draft` gains `publish`, `schedule` and `apply` (a staged
  copy onto its live post), each read back after the write. The desk's Publish, Schedule and Apply
  buttons act directly at that level; below it they open WordPress, as in 0.1.0. Within the level
  there is no approval card.
- **Scheduled runs write drafts.** A recurring-draft automation publishes only when it says
  `publish: true` and the level is Publish, and then only the post that same run created.
- **Passwords go only to their own site.** A password is sent only to the host it was last checked
  against, over https, never across a redirect. A changed address does not take the old password
  along.
- **Remove** revokes the application password in WordPress (optional), empties the group and deletes
  the site file. Public sites are added with their address only.
- **Passwords saved in the settings show on the page without a restart.**
- **Connector 0.2.1:** the WPBrücke reader role. It reads posts, pages and drafts over REST and asks
  the connector for draft preview links, so a Read-level site proves drafts; WordPress refuses its
  every other write over REST and XML-RPC, except revoking its own application password. 0.2.1
  stores only read rights in the role (`read`, `level_0`, `read_private_posts`,
  `read_private_pages`) and adds the edit rights that show drafts only while a REST read runs, so a
  connector folder deleted without deactivation leaves no edit rights behind. Uploading 0.2.1 over
  0.2.0 removes the edit rights 0.2.0 stored; WPBrücke refuses the reader role on 0.2.0.
  Deactivating the connector removes the role (on every site of a network), so a reader user is
  left with no rights.
- **Removed:** the settings `key1` … `key12`, `agent_writes` and `publishing_default`, and the site
  fields `publishing` and `never_publish_never_send` ("never publish, never send"). The role replaces
  them. On the first load each 0.1.0 site file fills its group's address and user, and the page asks
  you to confirm its level.

## 0.1.0

First release: drafts, browser proof at three widths, SEO, the review desk, watchers, recurring
drafts, the WordPress connector and the GeneratePress pack.
