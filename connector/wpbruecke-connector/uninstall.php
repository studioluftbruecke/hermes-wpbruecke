<?php
/**
 * Uninstall: remove the reader and drafter roles and every option the connector wrote.
 *
 * Users who had only one of these roles keep their account but have no role afterwards.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'WP_UNINSTALL_PLUGIN' ) ) {
	exit;
}

/**
 * Remove the roles and options on the current site.
 */
function wpbruecke_uninstall_site() {
	remove_role( 'wpbruecke_reader' );
	remove_role( 'wpbruecke_drafter' );
	delete_option( 'wpbruecke_preview_secret' );
	delete_option( 'wpbruecke_staged_brand' );
	delete_option( 'wpbruecke_connector_version' );
}

if ( is_multisite() ) {
	foreach ( get_sites( array( 'fields' => 'ids' ) ) as $wpbruecke_site_id ) {
		switch_to_blog( $wpbruecke_site_id );
		wpbruecke_uninstall_site();
		restore_current_blog();
	}
} else {
	wpbruecke_uninstall_site();
}
