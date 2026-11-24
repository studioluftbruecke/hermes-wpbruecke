=== WPBrücke Connector ===
Contributors: studioluftbruecke
Tags: drafts, preview, review, seo, generatepress
Requires at least: 6.5
Tested up to: 7.1
Requires PHP: 7.4
Stable tag: 0.1.0
License: MIT
License URI: https://opensource.org/licenses/MIT

Lets an AI assistant write drafts on your site while you keep the only hand on publishing.

== Description ==

The WPBrücke Connector is the WordPress half of WPBrücke, a plugin for the Hermes agent. The
assistant writes drafts over the WordPress REST API. This plugin adds the few things WordPress
does not have on its own:

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
* **Abilities** (`wpbruecke/site-facts`, `wpbruecke/gp-brand-get`, `wpbruecke/gp-brand-stage`) when
  your WordPress has the Abilities API.

= It never publishes on its own =

Nothing in this plugin publishes, schedules or changes a live post by itself. The review screen only
acts when a logged-in person clicks a button, and every button checks that person's own WordPress
rights and a security nonce first. A person who cannot publish does not see Publish. Opening the
screen changes nothing. The assistant's REST routes only read facts, make preview links and stage a
colour palette for review.

= Privacy =

The plugin makes no requests to other servers and sends no data anywhere.

== Installation ==

1. In wp-admin, go to Plugins → Add New → Upload Plugin and choose `wpbruecke-connector.zip`.
2. Click Install Now, then Activate.
3. Give the assistant's WordPress user the role "WPBrücke drafter".

== Frequently Asked Questions ==

= What happens when I deactivate it? =

The preview secret is replaced, so every preview link handed out so far stops working. The role and
fields stay until you delete the plugin.

= What happens when I delete it? =

The drafter role and the plugin's options are removed. Users who had only that role keep their
account without a role; give them another one. Post meta values stay in the database.

= What does "Apply" do with a draft copy? =

It copies the title, content, excerpt, featured image and SEO fields of the copy onto the live post
it was made from, keeps the live post's status, and moves the copy to the trash. WordPress keeps a
revision of the live post's old version.

== REST API ==

Namespace `wpbruecke/v1`:

* `GET /info` (users who can edit posts): version, role, SEO plugin, review screen URL, and update
  counts for users who can update plugins.
* `POST /preview-link` with `post_id` (users who can edit that post): a signed preview link and its
  expiry.
* `POST /gp/brand` with `colors` and `fonts` (users who can edit posts): stores a palette for review.
  It is applied only from the review screen.

== Changelog ==

= 0.1.0 =
* First version: drafter role, preview links, SEO fields, update counts, review screen, abilities.
