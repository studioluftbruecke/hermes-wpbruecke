<?php
/**
 * Plugin Name:       WPBrücke Connector
 * Description:       Companion for the WPBrücke Hermes plugin: a reader role (reads drafts, every write refused over REST and XML-RPC), a drafter role (Contributor plus uploads), two WooCommerce shop roles (draft products only, or also publish products), a FluentCRM screen where an administrator decides what Hermes may do in FluentCRM, signed preview links for drafts, SEO title and description fields, update counts, the page builder of each post, and a review screen where your own login publishes or applies staged changes. It never publishes on its own.
 * Version:           0.5.0
 * Requires at least: 6.5
 * Requires PHP:      7.4
 * Author:            Studio Luftbrücke
 * License:           MIT
 * License URI:       https://opensource.org/licenses/MIT
 * Text Domain:       wpbruecke-connector
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPBRUECKE_CONNECTOR_VERSION', '0.5.0' );
define( 'WPBRUECKE_CONNECTOR_FILE', __FILE__ );

require_once __DIR__ . '/includes/role.php';
require_once __DIR__ . '/includes/reader.php';
require_once __DIR__ . '/includes/preview.php';
require_once __DIR__ . '/includes/seo.php';
require_once __DIR__ . '/includes/updates.php';
require_once __DIR__ . '/includes/review.php';
require_once __DIR__ . '/includes/abilities.php';
require_once __DIR__ . '/includes/rest.php';
require_once __DIR__ . '/includes/builders.php';
require_once __DIR__ . '/includes/elementor.php';
require_once __DIR__ . '/includes/shop.php';
require_once __DIR__ . '/includes/fluentcrm.php';

/**
 * Activation: the reader and drafter roles and the preview secret.
 */
function wpbruecke_activate() {
	wpbruecke_sync_role();
	wpbruecke_sync_reader_role();
	wpbruecke_preview_secret_create();
	update_option( 'wpbruecke_connector_version', WPBRUECKE_CONNECTOR_VERSION, false );
}
register_activation_hook( __FILE__, 'wpbruecke_activate' );

/**
 * Deactivation on one site: rotate the preview secret, so every preview link handed out so far stops
 * working, remove the reader role, whose write guards stop with the plugin (its users keep no rights),
 * take back the FluentCRM permissions the connector's screen granted, and forget the synced version,
 * so the next activation or request syncs the roles again.
 */
function wpbruecke_deactivate_site() {
	wpbruecke_preview_secret_rotate();
	wpbruecke_remove_reader_role();
	wpbruecke_fcrm_deactivate();
	delete_option( 'wpbruecke_connector_version' );
}

/**
 * Deactivation: on every site of the network when deactivated network-wide, else on this site.
 *
 * @param bool $network_wide Whether the plugin is deactivated for the whole network.
 */
function wpbruecke_deactivate( $network_wide = false ) {
	if ( $network_wide && is_multisite() ) {
		foreach ( get_sites( array( 'fields' => 'ids' ) ) as $site_id ) {
			switch_to_blog( $site_id );
			wpbruecke_deactivate_site();
			restore_current_blog();
		}
		return;
	}
	wpbruecke_deactivate_site();
}
register_deactivation_hook( __FILE__, 'wpbruecke_deactivate' );

/**
 * A zip uploaded over an older copy does not run the activation hook: sync the roles and the secret
 * once per version, on the next wp-admin or REST request. From 0.2.0 this replaces the reader role's
 * stored caps with the read caps only.
 */
function wpbruecke_maybe_upgrade() {
	if ( get_option( 'wpbruecke_connector_version' ) !== WPBRUECKE_CONNECTOR_VERSION ) {
		wpbruecke_activate();
	}
}
add_action( 'admin_init', 'wpbruecke_maybe_upgrade' );
add_action( 'rest_api_init', 'wpbruecke_maybe_upgrade' );

/**
 * The SEO plugin that owns the page head, or null.
 *
 * @return string|null yoast, rankmath, seopress or null.
 */
function wpbruecke_seo_plugin() {
	if ( defined( 'WPSEO_VERSION' ) ) {
		return 'yoast';
	}
	if ( defined( 'RANK_MATH_VERSION' ) ) {
		return 'rankmath';
	}
	if ( defined( 'SEOPRESS_VERSION' ) ) {
		return 'seopress';
	}
	return null;
}

/**
 * The review screen's URL, optionally for one post.
 *
 * @param int $post_id Post ID or 0.
 * @return string
 */
function wpbruecke_review_url( $post_id = 0 ) {
	$args = array( 'page' => 'wpbruecke-review' );
	if ( $post_id ) {
		$args['post'] = (int) $post_id;
	}
	return add_query_arg( $args, admin_url( 'admin.php' ) );
}
