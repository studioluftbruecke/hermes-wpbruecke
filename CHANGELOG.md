# Changelog

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
