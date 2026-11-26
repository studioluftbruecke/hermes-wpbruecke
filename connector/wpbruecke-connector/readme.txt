=== WPBrücke Connector ===
Contributors: studioluftbruecke
Tags: drafts, preview, review, seo, elementor
Requires at least: 6.5
Tested up to: 7.1
Requires PHP: 7.4
Stable tag: 0.5.0
License: MIT
License URI: https://opensource.org/licenses/MIT

The companion for Hermes: an AI assistant writes drafts, you review and publish them with your own login. Calls no outside service.

== Description ==

The WPBrücke Connector is the WordPress half of WPBrücke, a plugin for the Hermes agent
(https://hermes-agent.nousresearch.com). Hermes writes drafts over the WordPress REST API, logged in as
a WordPress user you create for it, with an application password you give it. This plugin adds the
few things WordPress does not have on its own:

* **A reader role** (`wpbruecke_reader`, "WPBrücke reader"): reads posts, pages and drafts over the
  REST API, including drafts in edit context, and nothing else. The role stores only read rights;
  this plugin adds the edit rights WordPress asks for drafts only while it serves a REST read. It
  refuses every write for that role over REST and XML-RPC (403), whatever the request. It lets two
  requests through: the user deleting its own application password, so the assistant can revoke its
  own key, and this plugin's own preview-link request, which signs a link and stores nothing.
* **A drafter role** (`wpbruecke_drafter`): the Contributor's rights plus media uploads. It can
  never publish, never edit a published post and never edit other people's posts, so WordPress
  itself refuses those actions for the assistant's user.
* **Signed preview links** for drafts, pending and scheduled posts. A link works for two hours, and
  stops working sooner when the post is saved again, published, or this plugin is deactivated.
  Preview pages are sent with "noindex" and "no-store", so they are not indexed or cached.
* **SEO title and description fields** for posts and pages. Without an SEO plugin, this plugin prints
  them into the page head. With Yoast SEO, Rank Math or SEOPress active, it leaves the head to them and
  lets the drafter fill in their description field instead.
* **Update counts** from WordPress' own update checks, including premium plugins and themes that use
  their own updaters. Only users who may update plugins see them.
* **A review screen** (`wp-admin/admin.php?page=wpbruecke-review&post=ID`) with buttons to publish,
  schedule, apply a draft copy to its live post, and apply a staged GeneratePress colour palette.
* **The page builder of each post and page** (REST field `wpbruecke_builder`): block editor, classic
  editor, Elementor, Divi, WPBakery, Beaver Builder, Bricks, Oxygen or Breakdance, read from the
  post's builder meta. Only users who may edit the post see it. The assistant uses it to leave a
  builder page's layout alone.
* **Elementor pages through staged copies**: read a page's element tree, copy the page to a draft
  with Elementor's own meta copy, change texts, links and images of existing heading, text editor,
  button, image, icon list and call-to-action widgets through Elementor's document save, and apply the
  copy to the live page. No element is added, removed or moved. A user without the `unfiltered_html`
  right is refused where WordPress would strip HTML the edit does not touch.
* **Two WooCommerce shop roles** for the assistant's user. "WPBrücke shop drafter"
  (`wpbruecke_shop_drafter`): the Contributor's rights plus uploads and editing its own products, and
  never `publish_products`. It writes draft products through this plugin's shop route, which always
  saves a draft. A guard on WooCommerce's own product save refuses a publish, schedule or private
  status for any user who may edit products but not publish them, also over WooCommerce's REST API.
  "WPBrücke shop publisher" (`wpbruecke_shop_publisher`): the Author's rights plus editing and
  publishing its own products. Neither role touches orders, customers or other users' products.
* **A FluentCRM screen** (Settings → WPBrücke FluentCRM, administrators only) where you pick the
  assistant's user and tick what it may do in FluentCRM: view campaigns, write draft campaigns, send
  test emails to the one address you type there, and schedule and send campaigns. While the last box
  is off, the user holds only FluentCRM's "Read emails" permission, so FluentCRM itself refuses it
  every send. Drafts, previews and test emails go through three routes of this plugin that lend
  FluentCRM's "Write and send emails" permission for one fixed FluentCRM call each (previews and test
  emails also "Read contacts", so they render against the Hermes user's own contact, never a
  customer's). Works with free
  FluentCRM and FluentCRM Pro.
* **Abilities** (`wpbruecke/site-facts`, `wpbruecke/gp-brand-get`, `wpbruecke/gp-brand-stage`) when
  your WordPress has the Abilities API.

= It never publishes on its own =

Nothing in this plugin publishes, schedules or changes a live post by itself. The review screen only
acts when a logged-in person clicks a button, and every button checks that person's own WordPress
rights and a security nonce first. A person who cannot publish does not see Publish. Opening the
screen changes nothing. The assistant's REST routes read facts, make preview links, stage a colour
palette for review, stage and save Elementor draft copies, save draft products, and save, preview
and test-send FluentCRM draft campaigns. The FluentCRM routes never send a campaign. One route
changes a live post: the
Elementor apply, which needs the caller's own right to publish and edit that post, the same right
WordPress' REST API already gives that user. It refuses when someone edited the live page after the
copy was staged.

= Privacy and outside services =

This plugin calls no outside service. It makes no requests to other servers, loads no remote scripts,
fonts or images, and sends no data anywhere. Hermes connects to your site, over your site's own REST
API, with the application password you created for its user; you can revoke that password at any time
on that user's profile screen.

== Installation ==

1. In wp-admin, go to Plugins → Add New Plugin, search for "WPBrücke Connector", click Install Now,
   then Activate. (Or choose Upload Plugin with `wpbruecke-connector.zip`; the folder name is the same,
   so a zip install gets the updates from here too.)
2. Create a WordPress user for the assistant and give it the role "WPBrücke reader" (read only) or
   "WPBrücke drafter" (drafts and images); on a WooCommerce shop, "WPBrücke shop drafter" (draft
   products) or "WPBrücke shop publisher" (also live products). Never give it an administrator
   account.
3. On that user's profile screen (Users → All Users → Edit), create an application password, and
   enter the site address, the user name and that password in the WPBrücke plugin of Hermes.
4. With FluentCRM: Settings → WPBrücke FluentCRM, pick that user, tick the boxes you want, Save.
   Until you save, the assistant does nothing in FluentCRM.

== Frequently Asked Questions ==

= Do I need Hermes? =

Yes. This plugin is the companion for the WPBrücke plugin of the Hermes agent. On its own it adds
four roles, preview links, SEO fields, the review screen and the FluentCRM screen, and nothing uses
them until Hermes connects.

= Does it contact any outside service? =

No. It calls no outside service and sends no data anywhere. Hermes talks to your site, not the other
way round.

= Can the assistant publish? =

Not with the reader, drafter and shop drafter roles: the reader cannot write at all, and the drafter
and shop drafter can never publish or edit a published post or product. Publishing, scheduling and
applying a copy happen on the review screen, when you click with your own login. The shop publisher
role publishes its own products, because you chose that role for it. In FluentCRM the assistant sends
a campaign only after you tick "Schedule and send campaigns" for its user.

= What happens when I deactivate it? =

The preview secret is replaced, so every preview link handed out so far stops working. The reader
role is removed, because its write refusal only works while this plugin runs: a reader user has no
rights until you activate the plugin again, which brings the role back. The two shop roles are
removed too, because their publish guard only works while this plugin runs. The FluentCRM
permissions the FluentCRM screen granted are taken back. Deactivated for a whole network, it does
this on every site. The drafter role and the fields stay until you delete the plugin.

= What happens when I delete it? =

The reader, drafter and shop roles and the plugin's options, the FluentCRM screen's settings
included, are removed. Users who had only one of those roles keep their account without a role; give
them another one. Post meta values stay in the database.

= What does "Apply" do with a draft copy? =

It copies the title, content, excerpt, featured image and SEO fields of the copy onto the live post
it was made from, keeps the live post's status, and moves the copy to the trash. WordPress keeps a
revision of the live post's old version.

== Screenshots ==

1. The review screen for an Elementor page: the draft copy Hermes staged, next to the live page it changes, and the Apply button.
2. After Apply: the copy is now the live page, WordPress kept a revision of the old version, and the copy moved to the trash.
3. The two roles for the assistant's WordPress user, WPBrücke reader and WPBrücke drafter, in the Users screen.

== REST API ==

Namespace `wpbruecke/v1`:

* `GET /info` (users who can edit posts, the reader included): version, role, `reader` (true when
  the reader role exists), SEO plugin, review screen URL, and update counts for users who can update
  plugins.
* `POST /preview-link` with `post_id` (users who can edit that post, the reader included): a signed
  preview link and its expiry.
* `POST /gp/brand` with `colors` and `fonts` (users who can edit posts): stores a palette for review.
  It is applied only from the review screen.

REST field `wpbruecke_builder` on posts and pages, in the `edit` context only (users who can edit that
post): `{id, evidence}`, where `id` is `blocks`, `classic`, `elementor`, `divi`, `wpbakery`, `beaver`,
`bricks`, `oxygen`, `breakdance` or `unknown_builder`.

Elementor routes (Elementor active; the reader role is refused on every POST route):

* `GET /elementor/<id>` (users who can edit that post): the element tree, Elementor's version, and per
  element whether it is editable.
* `POST /elementor/<id>/stage` (users who can edit that post): a draft copy owned by the caller.
* `POST /elementor/<id>` with `edits` (users who can edit that draft or pending post): changes texts,
  links and images through Elementor's document save.
* `POST /elementor/<copy>/apply` (users who can publish and edit the live post): copies the layout
  onto the live post, clears Elementor's caches for it and moves the copy to the trash. Answers 409
  `live_changed` when the live post changed since staging.
* Without Elementor every route answers 409 `wpbruecke_no_elementor`.

WooCommerce route (WooCommerce active; the reader role is refused):

* `POST /shop/product` (users who can edit products): a new draft product, or with `id` an edit of a
  draft product the caller may edit. Name, description, short description, prices, category ids and
  featured image. The status is always draft. Answers 409 `wpbruecke_not_draft` for a product that is
  not a draft, 403 `wpbruecke_cannot_edit_product` for another user's product.

FluentCRM routes (FluentCRM active; the POST routes need the named box on the FluentCRM screen for the
calling user):

* `GET /fluentcrm/permissions` (users who can edit posts): the calling user's boxes, and whether the
  screen was saved for that user.
* `POST /fluentcrm/campaign` ("Write draft campaigns"): creates a draft campaign or updates a draft.
* `POST /fluentcrm/preview` ("Write draft campaigns"): FluentCRM's rendered email of a saved campaign.
* `POST /fluentcrm/test` ("Send test emails"): one test copy to the address saved on the screen; an
  address in the request is ignored.
* A box that is off answers 403 `wpbruecke_fcrm_box_off`; without FluentCRM, 409 `wpbruecke_fcrm_inactive`.

== Changelog ==

= 0.5.0 =
* Two WooCommerce shop roles, "WPBrücke shop drafter" (draft products only) and "WPBrücke shop
  publisher" (also publishes its own products). Removed on deactivation and on delete.
* `POST /shop/product`: saves draft products through WooCommerce's product objects, always as a draft.
* A guard on WooCommerce's product save refuses a publish, schedule or private status for a user who
  may edit products but not publish them, also over WooCommerce's own REST API.
* The FluentCRM screen (Settings → WPBrücke FluentCRM): an administrator picks the assistant's user and
  ticks what it may do in FluentCRM. Free FluentCRM and FluentCRM Pro.
* Four FluentCRM routes: the user's permissions, save a draft campaign, preview it, send a test copy to
  the saved address. None of them sends a campaign.
* Deactivation takes back the FluentCRM permissions the screen granted.
* Passes Plugin Check's "Plugin repo" checks without errors.

= 0.4.0 =
* Elementor routes: read a page's element tree, stage a draft copy with Elementor's own meta copy
  (template and featured image included), save edits to texts, links and images of existing heading,
  text editor, button, image, icon list and call-to-action widgets through Elementor's document save,
  and apply a staged copy to its live page with Elementor's caches cleared. No element is added,
  removed or moved. Other widgets and V4 atomic elements stay read-only.
* The review screen's Apply copy uses the same apply for Elementor copies.
* Apply is refused when the live page changed after the copy was staged (`live_changed`).
* A user without `unfiltered_html` is refused where WordPress would strip HTML the edit does not
  touch (`kses_would_strip`).
* The reader role is refused on every new POST route.

= 0.3.0 =
* REST field `wpbruecke_builder` on posts and pages (edit context only): which page builder made the
  post, read from its builder meta on the server. Beaver Builder, Bricks, Oxygen and Breakdance keep
  their layout in meta the REST API does not show, so the assistant can name them only through this
  field.
* Plugin and connector now share one version number.

= 0.2.1 =
* The reader role stores only read rights (`read`, `level_0`, `read_private_posts`,
  `read_private_pages`). The edit rights that show drafts in edit context are added only while a REST
  read runs. Updating from 0.2.0 removes the stored edit rights on the next wp-admin or REST request.
* The reader may ask for a signed preview link (`POST /preview-link`), so a Read-level site can
  prove a draft.
* XML-RPC for the reader allows `wp.getProfile` only.
* Network deactivation removes the reader role on every site.

= 0.2.0 =
* Reader role "WPBrücke reader": reads drafts over REST; every REST and XML-RPC write is refused,
  except deleting its own application password. Removed on deactivation and on delete.
* `GET /info` answers `reader: true` when the role exists.
* Roles are synced after an upgrade on the next wp-admin or REST request.

= 0.1.0 =
* First version: drafter role, preview links, SEO fields, update counts, review screen, abilities.
