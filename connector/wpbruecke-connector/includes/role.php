<?php
/**
 * The wpbruecke_drafter role: Contributor rights plus media uploads, never publishing or live edits.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPBRUECKE_ROLE', 'wpbruecke_drafter' );

/**
 * Rights the drafter role must never hold, whatever the Contributor role holds on this site.
 *
 * @return string[]
 */
function wpbruecke_forbidden_caps() {
	return array(
		'publish_posts',
		'publish_pages',
		'edit_published_posts',
		'edit_published_pages',
		'edit_others_posts',
		'edit_others_pages',
		'delete_published_posts',
		'delete_published_pages',
		'delete_others_posts',
		'delete_others_pages',
		'edit_private_posts',
		'read_private_posts',
		'unfiltered_html',
	);
}

/**
 * Contributor's rights (as this site defines them) plus upload_files, minus the forbidden list.
 *
 * @return array<string,bool>
 */
function wpbruecke_drafter_caps() {
	$caps        = array(
		'read'         => true,
		'edit_posts'   => true,
		'delete_posts' => true,
		'level_1'      => true,
		'level_0'      => true,
	);
	$contributor = get_role( 'contributor' );
	if ( $contributor && is_array( $contributor->capabilities ) ) {
		$caps = $contributor->capabilities;
	}
	$caps['upload_files'] = true;
	foreach ( wpbruecke_forbidden_caps() as $cap ) {
		unset( $caps[ $cap ] );
	}
	return $caps;
}

/**
 * Create the role, or bring an existing one back to exactly the drafter rights.
 */
function wpbruecke_sync_role() {
	$caps = wpbruecke_drafter_caps();
	$role = get_role( WPBRUECKE_ROLE );
	if ( ! $role ) {
		add_role( WPBRUECKE_ROLE, __( 'WPBrücke drafter', 'wpbruecke-connector' ), $caps );
		return;
	}
	foreach ( array_keys( (array) $role->capabilities ) as $cap ) {
		if ( empty( $caps[ $cap ] ) ) {
			$role->remove_cap( $cap );
		}
	}
	foreach ( $caps as $cap => $grant ) {
		if ( $grant && empty( $role->capabilities[ $cap ] ) ) {
			$role->add_cap( $cap );
		}
	}
}
