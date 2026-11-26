<?php
/**
 * Uninstall: remove the connector's roles (reader, drafter, shop drafter, shop publisher) and every
 * option the connector wrote. Deactivation already removed the reader and shop roles and the FluentCRM
 * screen's option; uninstall removes them again in case a deactivation hook did not finish.
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
	remove_role( 'wpbruecke_shop_drafter' );
	remove_role( 'wpbruecke_shop_publisher' );
	delete_option( 'wpbruecke_preview_secret' );
	delete_option( 'wpbruecke_staged_brand' );
	delete_option( 'wpbruecke_connector_version' );
	delete_option( 'wpbruecke_shop_roles' );
	delete_option( 'wpbruecke_fluentcrm' );
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
