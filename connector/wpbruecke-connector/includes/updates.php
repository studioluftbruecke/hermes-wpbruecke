<?php
/**
 * Update counts read from WordPress' own update transients.
 *
 * Premium plugins and themes (GP Premium, GenerateBlocks Pro and the like) fill the same transients
 * through their own updaters, so they are counted too. Nothing here contacts another server.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * {count, core, plugins[{slug, installed, latest}], themes[{slug, installed, latest}]}.
 *
 * @return array
 */
function wpbruecke_updates_summary() {
	$out = array(
		'count'   => 0,
		'core'    => null,
		'plugins' => array(),
		'themes'  => array(),
	);

	$plugins = get_site_transient( 'update_plugins' );
	if ( is_object( $plugins ) && ! empty( $plugins->response ) && is_array( $plugins->response ) ) {
		$installed_list = null;
		foreach ( $plugins->response as $file => $info ) {
			$info = (object) $info;
			$file = (string) $file;
			$slug = ! empty( $info->slug ) ? (string) $info->slug : ( false !== strpos( $file, '/' ) ? dirname( $file ) : basename( $file, '.php' ) );
			$installed = null;
			if ( isset( $plugins->checked ) && is_array( $plugins->checked ) && isset( $plugins->checked[ $file ] ) ) {
				$installed = (string) $plugins->checked[ $file ];
			} else {
				if ( null === $installed_list ) {
					if ( ! function_exists( 'get_plugins' ) ) {
						require_once ABSPATH . 'wp-admin/includes/plugin.php';
					}
					$installed_list = get_plugins();
				}
				if ( isset( $installed_list[ $file ]['Version'] ) ) {
					$installed = (string) $installed_list[ $file ]['Version'];
				}
			}
			$out['plugins'][] = array(
				'slug'      => sanitize_key( $slug ),
				'installed' => $installed,
				'latest'    => isset( $info->new_version ) ? (string) $info->new_version : null,
			);
		}
	}

	$themes = get_site_transient( 'update_themes' );
	if ( is_object( $themes ) && ! empty( $themes->response ) && is_array( $themes->response ) ) {
		foreach ( $themes->response as $stylesheet => $info ) {
			$info       = (array) $info;
			$stylesheet = (string) $stylesheet;
			$installed  = null;
			if ( isset( $themes->checked ) && is_array( $themes->checked ) && isset( $themes->checked[ $stylesheet ] ) ) {
				$installed = (string) $themes->checked[ $stylesheet ];
			} else {
				$theme = wp_get_theme( $stylesheet );
				if ( $theme->exists() ) {
					$installed = (string) $theme->get( 'Version' );
				}
			}
			$out['themes'][] = array(
				'slug'      => sanitize_key( isset( $info['theme'] ) ? (string) $info['theme'] : $stylesheet ),
				'installed' => $installed,
				'latest'    => isset( $info['new_version'] ) ? (string) $info['new_version'] : null,
			);
		}
	}

	$core = get_site_transient( 'update_core' );
	if ( is_object( $core ) && ! empty( $core->updates ) && is_array( $core->updates ) ) {
		foreach ( $core->updates as $update ) {
			$update = (object) $update;
			if ( isset( $update->response ) && 'upgrade' === $update->response && ! empty( $update->current ) ) {
				$out['core'] = array(
					'installed' => (string) get_bloginfo( 'version' ),
					'latest'    => (string) $update->current,
				);
				break;
			}
		}
	}

	$out['count'] = count( $out['plugins'] ) + count( $out['themes'] ) + ( $out['core'] ? 1 : 0 );
	return $out;
}
