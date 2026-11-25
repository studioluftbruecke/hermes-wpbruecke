<?php
/**
 * The wpbruecke_reader role: reads posts, pages and drafts over REST and writes nothing.
 *
 * The role stores only read rights in the database: read, level_0, read_private_posts and
 * read_private_pages. WordPress shows drafts in edit context only to users with the edit caps, so the
 * user_has_cap filter below adds the edit caps while a REST read is served, and three guards keep them
 * from ever writing:
 * - rest_pre_dispatch refuses every REST method but GET and HEAD (two exceptions below);
 * - user_has_cap adds the edit caps only inside a REST read and leaves only the read caps everywhere
 *   else (wp-admin, XML-RPC, front end, a REST write);
 * - xmlrpc_call refuses every XML-RPC method but wp.getProfile.
 * The two allowed POST or DELETE requests: DELETE of the reader's own application password by its
 * uuid, so the key can revoke itself (it only removes access), and the connector's own
 * POST wpbruecke/v1/preview-link, which signs a preview link for a draft and stores nothing.
 *
 * When this file is not loaded (the plugin folder deleted or renamed without deactivation), nothing
 * adds the edit caps: a reader user keeps the stored read rights only, like a Subscriber who can also
 * read private posts. Deactivating or deleting the connector removes the role itself.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPBRUECKE_READER_ROLE', 'wpbruecke_reader' );

/**
 * Capabilities the role stores in the database: read rights only.
 *
 * @return array<string,bool>
 */
function wpbruecke_reader_caps() {
	return array(
		'read'               => true,
		'level_0'            => true,
		'read_private_posts' => true,
		'read_private_pages' => true,
	);
}

/**
 * Edit caps the user_has_cap filter adds while a REST read is served, so REST shows drafts in edit
 * context. Never stored in the role.
 *
 * @return string[]
 */
function wpbruecke_reader_rest_read_caps() {
	return array(
		'edit_posts',
		'edit_others_posts',
		'edit_published_posts',
		'edit_private_posts',
		'edit_pages',
		'edit_others_pages',
		'edit_published_pages',
		'edit_private_pages',
	);
}

/**
 * Outside a REST read the role keeps only these.
 *
 * @return string[]
 */
function wpbruecke_reader_always_caps() {
	return array_keys( wpbruecke_reader_caps() );
}

/**
 * Create the role, or bring an existing one back to exactly the stored read caps (an upgrade from
 * 0.2.0 removes the edit caps that version stored).
 */
function wpbruecke_sync_reader_role() {
	$caps = wpbruecke_reader_caps();
	$role = get_role( WPBRUECKE_READER_ROLE );
	if ( ! $role ) {
		add_role( WPBRUECKE_READER_ROLE, __( 'WPBrücke reader', 'wpbruecke-connector' ), $caps );
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

/**
 * Remove the role (deactivation and uninstall).
 */
function wpbruecke_remove_reader_role() {
	remove_role( WPBRUECKE_READER_ROLE );
}

/**
 * Whether a user (object or ID) has the reader role.
 *
 * @param WP_User|int $user User.
 * @return bool
 */
function wpbruecke_is_reader( $user ) {
	if ( ! $user instanceof WP_User ) {
		$user = $user ? get_userdata( (int) $user ) : false;
	}
	return $user && in_array( WPBRUECKE_READER_ROLE, (array) $user->roles, true );
}

/**
 * The refusal text for every refused write.
 *
 * @return string
 */
function wpbruecke_reader_refusal_message() {
	return 'This user has the WPBrücke reader role: it can read, never write.';
}

/**
 * Whether a reader may run this REST request: GET and HEAD, the connector's own
 * POST wpbruecke/v1/preview-link (a signed token, no stored content), and DELETE of one of its own
 * application passwords by uuid (`users/me/...` or `users/<own id>/...`).
 *
 * @param string $method  HTTP method (after any _method or override header).
 * @param string $route   REST route, e.g. /wp/v2/users/me/application-passwords/<uuid>.
 * @param int    $user_id The reader's user ID.
 * @return bool
 */
function wpbruecke_reader_rest_allows( $method, $route, $user_id ) {
	$method = strtoupper( (string) $method );
	if ( 'GET' === $method || 'HEAD' === $method ) {
		return true;
	}
	if ( wpbruecke_reader_is_preview_link( $method, $route ) ) {
		return true;
	}
	if ( 'DELETE' !== $method ) {
		return false;
	}
	$uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
	if ( ! preg_match( '#^/wp/v2/users/(me|[0-9]+)/application-passwords/' . $uuid . '$#', (string) $route, $m ) ) {
		return false;
	}
	return 'me' === $m[1] || ( (int) $user_id > 0 && (string) (int) $user_id === $m[1] );
}

/**
 * Whether this is the connector's own preview-link request (POST, exact route).
 *
 * @param string $method HTTP method.
 * @param string $route  REST route.
 * @return bool
 */
function wpbruecke_reader_is_preview_link( $method, $route ) {
	return 'POST' === strtoupper( (string) $method ) && '/wpbruecke/v1/preview-link' === (string) $route;
}

/**
 * Whether a request reads, so the reader gets the edit caps for it: GET, HEAD and the preview link
 * (its permission check asks edit_post on the draft).
 *
 * @param string $method HTTP method.
 * @param string $route  REST route.
 * @return bool
 */
function wpbruecke_reader_rest_reads( $method, $route ) {
	$method = strtoupper( (string) $method );
	return 'GET' === $method || 'HEAD' === $method || wpbruecke_reader_is_preview_link( $method, $route );
}

/**
 * Whether a reader may run this XML-RPC method.
 *
 * @param string $method XML-RPC method name.
 * @return bool
 */
function wpbruecke_reader_xmlrpc_allows( $method ) {
	return 'wp.getProfile' === $method;
}

/**
 * A reader's caps: everywhere the stored read caps only; anything else (caps from another role, a
 * user cap, the role name) is set to false. Inside a REST read the edit caps are added.
 *
 * @param array<string,bool> $allcaps   Caps as WordPress computed them.
 * @param bool               $rest_read Whether a REST read is being served.
 * @return array<string,bool>
 */
function wpbruecke_reader_filter_caps( $allcaps, $rest_read ) {
	$keep = wpbruecke_reader_always_caps();
	foreach ( array_keys( (array) $allcaps ) as $cap ) {
		if ( ! in_array( $cap, $keep, true ) ) {
			$allcaps[ $cap ] = false;
		}
	}
	if ( $rest_read ) {
		foreach ( wpbruecke_reader_rest_read_caps() as $cap ) {
			$allcaps[ $cap ] = true;
		}
	}
	return $allcaps;
}

$GLOBALS['wpbruecke_reader_rest_read'] = false;

/**
 * REST guard: a reader's request that is not a read is refused before any route runs.
 *
 * @param mixed           $result  Response so far (null to continue).
 * @param WP_REST_Server  $server  Server.
 * @param WP_REST_Request $request Request.
 * @return mixed
 */
function wpbruecke_reader_rest_pre_dispatch( $result, $server, $request ) {
	$user_id = get_current_user_id();
	if ( ! wpbruecke_is_reader( $user_id ) ) {
		return $result;
	}
	$method = strtoupper( (string) $request->get_method() );
	if ( ! wpbruecke_reader_rest_allows( $method, $request->get_route(), $user_id ) ) {
		$GLOBALS['wpbruecke_reader_rest_read'] = false;
		return new WP_Error( 'wpbruecke_reader_read_only', wpbruecke_reader_refusal_message(), array( 'status' => 403 ) );
	}
	$GLOBALS['wpbruecke_reader_rest_read'] = wpbruecke_reader_rest_reads( $method, $request->get_route() );
	return $result;
}
add_filter( 'rest_pre_dispatch', 'wpbruecke_reader_rest_pre_dispatch', 0, 3 );

/**
 * Caps guard, last filter on user_has_cap.
 *
 * @param array<string,bool> $allcaps Caps.
 * @param string[]           $caps    Required primitive caps.
 * @param array              $args    Arguments.
 * @param WP_User            $user    User.
 * @return array<string,bool>
 */
function wpbruecke_reader_user_has_cap( $allcaps, $caps, $args, $user ) {
	if ( ! wpbruecke_is_reader( $user ) ) {
		return $allcaps;
	}
	return wpbruecke_reader_filter_caps( $allcaps, ! empty( $GLOBALS['wpbruecke_reader_rest_read'] ) );
}
add_filter( 'user_has_cap', 'wpbruecke_reader_user_has_cap', PHP_INT_MAX, 4 );

/**
 * XML-RPC guard: every method but wp.getProfile answers a 403 fault.
 *
 * @param string           $method Method name.
 * @param mixed            $args   Arguments.
 * @param wp_xmlrpc_server $server Server.
 */
function wpbruecke_reader_xmlrpc_call( $method, $args = null, $server = null ) {
	if ( ! wpbruecke_is_reader( get_current_user_id() ) || wpbruecke_reader_xmlrpc_allows( $method ) ) {
		return;
	}
	if ( is_object( $server ) && method_exists( $server, 'error' ) && class_exists( 'IXR_Error' ) ) {
		$server->error( new IXR_Error( 403, wpbruecke_reader_refusal_message() ) );
	}
	wp_die( esc_html( wpbruecke_reader_refusal_message() ), '', array( 'response' => 403 ) );
}
add_action( 'xmlrpc_call', 'wpbruecke_reader_xmlrpc_call', 0, 3 );
