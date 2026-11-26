<?php
/**
 * FluentCRM (v0.5): what the site's admin lets Hermes do in FluentCRM, and three routes that lend
 * FluentCRM's "Write and send emails" permission for one fixed ability call.
 *
 * The screen (Settings > WPBrücke FluentCRM, manage_options, a nonce on every save) picks the Hermes
 * user, never an administrator, and holds four boxes:
 *   1 View campaigns              FluentCRM's fcrm_read_emails (FluentCRM holds it)
 *   2 Write draft campaigns       opens the campaign and preview routes (this connector holds it)
 *   3 Send test emails            opens the test route, to the address typed on the screen (connector)
 *   4 Schedule and send campaigns FluentCRM's fcrm_manage_emails, its whole "Write and send emails"
 * Boxes 1 and 4 show what the user really holds in FluentCRM (a grant in FluentCRM Pro's Managers
 * screen shows here, and the reverse). A save reads the user's current FluentCRM list, adds or removes
 * only those two permissions and stores the merged list with PermissionManager::attachPermissions (it
 * replaces the whole list); it sets Pro's _fcrm_has_role marker while the list is not empty, and links a
 * FluentCRM contact of the user's own (status transactional), so SmartCodes in previews and test mails
 * never fill from a real customer.
 *
 * GET  wpbruecke/v1/fluentcrm/permissions  edit_posts: FcrmPermissions for the calling user only.
 * POST wpbruecke/v1/fluentcrm/campaign     box 2: fluent-crm/upsert-campaign on a draft (or a new one).
 * POST wpbruecke/v1/fluentcrm/preview      box 2: fluent-crm/render-email-preview of a saved campaign.
 * POST wpbruecke/v1/fluentcrm/test         box 3: fluent-crm/send-test-email to the saved address only.
 *
 * The request never names the ability, and the routes take only the keys listed per route. The lend
 * adds fcrm_manage_emails through the filter fluent_crm/user_permissions for the calling user, refreshes
 * FluentCRM's per-request permission cache, runs the ability, removes the filter and refreshes again
 * (without the refresh the lend has no effect, P33). Nothing is written to the user's permissions.
 * Preview and test render against the Hermes user's own contact: before each of them the route finds,
 * relinks or recreates it and names it as against_contact_id (FluentCRM otherwise falls back to the test
 * address's contact or the oldest contact, a real customer). Naming the contact needs fcrm_read_contacts
 * in FluentCRM, so those two calls lend it too; without an own contact they refuse 409 and nothing renders.
 * The reader role is refused on every POST route by includes/reader.php. Deactivating the connector
 * removes the FluentCRM permissions this screen granted (wpbruecke_fcrm_deactivate).
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

define( 'WPBRUECKE_FCRM_OPTION', 'wpbruecke_fluentcrm' );     // user id => {draft, test, test_to, saved, granted}
define( 'WPBRUECKE_FCRM_READ', 'fcrm_read_emails' );
define( 'WPBRUECKE_FCRM_MANAGE', 'fcrm_manage_emails' );
define( 'WPBRUECKE_FCRM_USER_META', 'FluentCrm\App\Models\User' );
define( 'WPBRUECKE_FCRM_PAGE', 'wpbruecke-fluentcrm' );

/**
 * Whether FluentCRM is loaded with the parts the screen and routes use.
 *
 * @return bool
 */
function wpbruecke_fcrm_ready() {
	return class_exists( '\FluentCrm\App\Services\PermissionManager' ) && function_exists( 'fluentcrm_get_meta' );
}

/**
 * The stored boxes of every user the screen was saved for.
 *
 * @return array<int,array>
 */
function wpbruecke_fcrm_all_boxes() {
	$all = get_option( WPBRUECKE_FCRM_OPTION, array() );
	return is_array( $all ) ? $all : array();
}

/**
 * One user's stored boxes, or null when the screen was never saved for that user.
 *
 * @param int $user_id User ID.
 * @return array|null
 */
function wpbruecke_fcrm_boxes( $user_id ) {
	$all = wpbruecke_fcrm_all_boxes();
	return isset( $all[ (int) $user_id ] ) && is_array( $all[ (int) $user_id ] ) ? $all[ (int) $user_id ] : null;
}

/**
 * The user's FluentCRM permission list as stored (no filter applied, so no lend shows), or the list
 * FluentCRM derives when the user has no stored row yet.
 *
 * @param int $user_id User ID.
 * @return string[]
 */
function wpbruecke_fcrm_stored_permissions( $user_id ) {
	$row = fluentcrm_get_meta( (int) $user_id, WPBRUECKE_FCRM_USER_META, 'manager_permissions' );
	if ( null !== $row && null !== $row->value ) {
		return array_values( array_filter( (array) $row->value, 'is_string' ) );
	}
	$perms = \FluentCrm\App\Services\PermissionManager::getUserPermissions( (int) $user_id );
	return array_values( array_filter( (array) $perms, 'is_string' ) );
}

/**
 * The save's merge: the current list with only fcrm_read_emails and fcrm_manage_emails changed.
 * Sending implies viewing (FluentCRM refuses fcrm_manage_emails without fcrm_read_emails).
 *
 * @param string[] $current The user's permissions now.
 * @param bool     $view    Box 1.
 * @param bool     $send    Box 4.
 * @return string[]
 */
function wpbruecke_fcrm_merged( array $current, $view, $send ) {
	$merged = array_values( array_diff( $current, array( WPBRUECKE_FCRM_READ, WPBRUECKE_FCRM_MANAGE ) ) );
	if ( $view || $send ) {
		$merged[] = WPBRUECKE_FCRM_READ;
	}
	if ( $send ) {
		$merged[] = WPBRUECKE_FCRM_MANAGE;
	}
	return array_values( array_unique( $merged ) );
}

/**
 * Which of the two permissions this connector granted, after a save: kept from before unless removed
 * now, plus what this save added. Deactivation removes exactly these.
 *
 * @param string[] $before  Granted by the connector before the save.
 * @param string[] $current The user's permissions before the save.
 * @param string[] $merged  The user's permissions after the save.
 * @return string[]
 */
function wpbruecke_fcrm_granted( array $before, array $current, array $merged ) {
	$ours  = array( WPBRUECKE_FCRM_READ, WPBRUECKE_FCRM_MANAGE );
	$added = array_diff( array_intersect( $merged, $ours ), $current );
	$kept  = array_intersect( $before, $merged, $ours );
	return array_values( array_unique( array_merge( $kept, $added ) ) );
}

/**
 * Store a merged list in FluentCRM and keep Pro's manager marker in step with it.
 *
 * @param WP_User  $user   User.
 * @param string[] $merged Permissions.
 */
function wpbruecke_fcrm_attach( $user, array $merged ) {
	\FluentCrm\App\Services\PermissionManager::attachPermissions( $user, $merged );
	// FluentCRM Pro's Managers screen lists users by this meta only.
	if ( $merged ) {
		update_user_meta( $user->ID, '_fcrm_has_role', 1 );
	} else {
		delete_user_meta( $user->ID, '_fcrm_has_role' );
	}
}

/**
 * The Hermes user's own FluentCRM contact (user_id linked): found by user, else by the user's email
 * (then linked), else created with status transactional, in no list and no tag.
 *
 * @param WP_User $user User.
 * @return int|null Contact ID.
 */
function wpbruecke_fcrm_own_contact( $user ) {
	if ( ! class_exists( '\FluentCrm\App\Models\Subscriber' ) || ! function_exists( 'FluentCrmApi' ) ) {
		return null;
	}
	$model   = '\FluentCrm\App\Models\Subscriber';
	$contact = $model::where( 'user_id', $user->ID )->first();
	if ( ! $contact && is_email( $user->user_email ) ) {
		$contact = $model::where( 'email', $user->user_email )->first();
	}
	if ( ! $contact ) {
		$contact = FluentCrmApi( 'contacts' )->createOrUpdate(
			array(
				'email'      => $user->user_email,
				'first_name' => $user->display_name ? $user->display_name : $user->user_login,
				'status'     => 'transactional',
			)
		);
	}
	if ( ! $contact ) {
		return null;
	}
	if ( (int) $contact->user_id !== (int) $user->ID ) {
		$contact->user_id = $user->ID;
		$contact->save();
	}
	return (int) $contact->id;
}

/**
 * Whether a user may be the Hermes user here: a real user who can edit posts and is no administrator.
 *
 * @param WP_User|false $user User.
 * @return bool
 */
function wpbruecke_fcrm_pickable( $user ) {
	return $user && ! user_can( $user, 'manage_options' ) && user_can( $user, 'edit_posts' );
}

/**
 * The screen's save for one user (the admin's click). Returns the notice code.
 *
 * @param int    $user_id User ID.
 * @param array  $boxes   {view, draft, test, send} booleans.
 * @param string $address The test address typed on the screen.
 * @return string
 */
function wpbruecke_fcrm_save( $user_id, array $boxes, $address ) {
	$user = get_user_by( 'id', (int) $user_id );
	if ( ! wpbruecke_fcrm_pickable( $user ) ) {
		return 'not_pickable';
	}
	$send    = ! empty( $boxes['send'] );
	$view    = ! empty( $boxes['view'] ) || $send;
	$current = wpbruecke_fcrm_stored_permissions( $user->ID );
	$merged  = wpbruecke_fcrm_merged( $current, $view, $send );
	wpbruecke_fcrm_attach( $user, $merged );

	$all     = wpbruecke_fcrm_all_boxes();
	$before  = isset( $all[ $user->ID ]['granted'] ) ? (array) $all[ $user->ID ]['granted'] : array();
	$address = sanitize_email( (string) $address );
	$test    = ! empty( $boxes['test'] ) && is_email( $address );

	$all[ $user->ID ] = array(
		'draft'   => ! empty( $boxes['draft'] ),
		'test'    => $test,
		'test_to' => $address,
		'saved'   => time(),
		'granted' => wpbruecke_fcrm_granted( $before, $current, $merged ),
		'contact' => wpbruecke_fcrm_own_contact( $user ),
	);
	update_option( WPBRUECKE_FCRM_OPTION, $all, false );
	return ( ! empty( $boxes['test'] ) && ! $test ) ? 'saved_no_address' : 'saved';
}

/**
 * FcrmPermissions for one user (the plan's shape, plus saved and user_id). The test address never
 * leaves WordPress: only whether one is saved.
 *
 * @param int $user_id User ID.
 * @return array
 */
function wpbruecke_fcrm_permissions_for( $user_id ) {
	$out = array(
		'fluentcrm'   => null,
		'version'     => null,
		'pro_version' => null,
		'saved'       => false,
		'user_id'     => (int) $user_id,
		'view'        => false,
		'draft'       => false,
		'test_email'  => false,
		'send'        => false,
		'screen'      => admin_url( 'options-general.php?page=' . WPBRUECKE_FCRM_PAGE ),
	);
	if ( ! wpbruecke_fcrm_ready() ) {
		return $out;
	}
	$perms = wpbruecke_fcrm_stored_permissions( $user_id );
	$boxes = wpbruecke_fcrm_boxes( $user_id );
	$view  = in_array( WPBRUECKE_FCRM_READ, $perms, true );
	$out   = array_merge(
		$out,
		array(
			'fluentcrm'   => defined( 'FLUENTCAMPAIGN' ) ? 'pro' : 'free',
			'version'     => defined( 'FLUENTCRM_PLUGIN_VERSION' ) ? FLUENTCRM_PLUGIN_VERSION : null,
			'pro_version' => defined( 'FLUENTCAMPAIGN_PLUGIN_VERSION' ) ? FLUENTCAMPAIGN_PLUGIN_VERSION : null,
			'saved'       => null !== $boxes,
			'view'        => $view,
			'draft'       => $view && ! empty( $boxes['draft'] ),
			'test_email'  => $view && ! empty( $boxes['test'] ) && is_email( isset( $boxes['test_to'] ) ? $boxes['test_to'] : '' ),
			'send'        => in_array( WPBRUECKE_FCRM_MANAGE, $perms, true ),
		)
	);
	return $out;
}

/**
 * Deactivation: take back the FluentCRM permissions this screen granted, and forget the boxes.
 * Works while FluentCRM is active; otherwise it edits FluentCRM's stored row directly.
 */
function wpbruecke_fcrm_deactivate() {
	foreach ( wpbruecke_fcrm_all_boxes() as $user_id => $boxes ) {
		$granted = isset( $boxes['granted'] ) ? array_values( (array) $boxes['granted'] ) : array();
		$user    = get_user_by( 'id', (int) $user_id );
		if ( ! $granted || ! $user ) {
			continue;
		}
		if ( wpbruecke_fcrm_ready() ) {
			$left = array_values( array_diff( wpbruecke_fcrm_stored_permissions( $user->ID ), $granted ) );
			wpbruecke_fcrm_attach( $user, $left );
		} else {
			wpbruecke_fcrm_deactivate_raw( $user->ID, $granted );
		}
	}
	delete_option( WPBRUECKE_FCRM_OPTION );
}

/**
 * FluentCRM is inactive: remove the permissions from its stored row with SQL.
 *
 * @param int      $user_id User ID.
 * @param string[] $granted Permissions to remove.
 */
function wpbruecke_fcrm_deactivate_raw( $user_id, array $granted ) {
	global $wpdb;
	$table = $wpdb->prefix . 'fc_meta';
	// phpcs:disable WordPress.DB.DirectDatabaseQuery -- FluentCRM's own table while FluentCRM is not loaded.
	if ( $wpdb->get_var( $wpdb->prepare( 'SHOW TABLES LIKE %s', $table ) ) !== $table ) {
		return;
	}
	$row = $wpdb->get_row( $wpdb->prepare( 'SELECT id, value FROM %i WHERE object_type = %s AND object_id = %d AND `key` = %s', $table, WPBRUECKE_FCRM_USER_META, $user_id, 'manager_permissions' ) );
	if ( ! $row ) {
		return;
	}
	$value = is_serialized( $row->value ) ? @unserialize( $row->value, array( 'allowed_classes' => false ) ) : $row->value; // phpcs:ignore WordPress.PHP.NoSilencedErrors,WordPress.PHP.DiscouragedPHPFunctions
	$left  = array_values( array_diff( array_filter( (array) $value, 'is_string' ), $granted ) );
	$wpdb->update( $table, array( 'value' => maybe_serialize( $left ) ), array( 'id' => (int) $row->id ) );
	// phpcs:enable
	if ( ! $left ) {
		delete_user_meta( $user_id, '_fcrm_has_role' );
	}
}

/* ------------------------------------------------------------------ the lend */

/**
 * Run one fluent-crm ability as the calling user with fcrm_manage_emails (and, for a render against the
 * own contact, fcrm_read_contacts) lent for that call only.
 *
 * @param string   $ability Ability name (fixed by the route, never by the request).
 * @param array    $input   Input.
 * @param string[] $lent    FluentCRM permissions lent for the call.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_fcrm_lend( $ability, array $input, array $lent = array( WPBRUECKE_FCRM_MANAGE ) ) {
	if ( ! function_exists( 'wp_get_ability' ) || ! wp_get_ability( $ability ) ) {
		return new WP_Error( 'wpbruecke_fcrm_no_ability', __( 'FluentCRM does not offer this ability on the site (WordPress 6.9 or newer, FluentCRM 3.3 or newer, and FluentCRM\'s abilities switched on).', 'wpbruecke-connector' ), array( 'status' => 409 ) );
	}
	$uid  = get_current_user_id();
	$lend = function ( $permissions, $user ) use ( $uid, $lent ) {
		if ( $user && (int) $user->ID === $uid ) {
			$permissions = array_values( array_unique( array_merge( (array) $permissions, $lent ) ) );
		}
		return $permissions;
	};
	add_filter( 'fluent_crm/user_permissions', $lend, 10, 2 );
	\FluentCrm\App\Services\PermissionManager::currentUserPermissions( false );
	try {
		$result = wp_get_ability( $ability )->execute( $input );
	} finally {
		remove_filter( 'fluent_crm/user_permissions', $lend, 10 );
		\FluentCrm\App\Services\PermissionManager::currentUserPermissions( false );
	}
	if ( is_wp_error( $result ) ) {
		$data   = $result->get_error_data();
		$status = is_array( $data ) && isset( $data['status'] ) ? (int) $data['status'] : 400;
		return new WP_Error( $result->get_error_code(), $result->get_error_message(), array( 'status' => $status >= 400 ? $status : 400 ) );
	}
	return rest_ensure_response( $result );
}

/**
 * The request's JSON body reduced to the keys a route takes.
 *
 * @param WP_REST_Request $request Request.
 * @param string[]        $keys    Allowed keys.
 * @return array
 */
function wpbruecke_fcrm_input( WP_REST_Request $request, array $keys ) {
	$body = $request->get_json_params();
	$body = is_array( $body ) ? $body : array();
	return array_intersect_key( $body, array_flip( $keys ) );
}

/**
 * A permission callback for one box: the caller is logged in, holds fcrm_read_emails and the box is on.
 *
 * @param string $box draft or test_email.
 * @return bool|WP_Error
 */
function wpbruecke_fcrm_box_open( $box ) {
	if ( ! is_user_logged_in() || ! current_user_can( 'edit_posts' ) ) {
		return false;
	}
	if ( ! wpbruecke_fcrm_ready() ) {
		return new WP_Error( 'wpbruecke_fcrm_inactive', __( 'FluentCRM is not active on this site.', 'wpbruecke-connector' ), array( 'status' => 409 ) );
	}
	$p = wpbruecke_fcrm_permissions_for( get_current_user_id() );
	if ( empty( $p[ $box ] ) ) {
		$label = 'draft' === $box ? __( 'Write draft campaigns', 'wpbruecke-connector' ) : __( 'Send test emails (with an address)', 'wpbruecke-connector' );
		/* translators: %s: the box label */
		return new WP_Error( 'wpbruecke_fcrm_box_off', sprintf( __( 'The site\'s admin has not switched "%s" on for this user (Settings > WPBrücke FluentCRM).', 'wpbruecke-connector' ), $label ), array( 'status' => 403 ) );
	}
	return true;
}

/**
 * A saved campaign of type campaign, or a WP_Error.
 *
 * @param int  $id         Campaign ID.
 * @param bool $draft_only Refuse a campaign that is no draft.
 * @return object|WP_Error
 */
function wpbruecke_fcrm_campaign( $id, $draft_only ) {
	$model    = '\FluentCrm\App\Models\Campaign';
	$campaign = class_exists( $model ) ? $model::find( (int) $id ) : null;
	if ( ! $campaign ) {
		return new WP_Error( 'wpbruecke_fcrm_no_campaign', __( 'No such campaign.', 'wpbruecke-connector' ), array( 'status' => 404 ) );
	}
	if ( $draft_only && 'draft' !== $campaign->status ) {
		return new WP_Error( 'wpbruecke_fcrm_not_draft', __( 'Only draft campaigns are written here; this one is not a draft.', 'wpbruecke-connector' ), array( 'status' => 409 ) );
	}
	return $campaign;
}

/**
 * POST fluentcrm/campaign: create a draft or update one, through upsert-campaign.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_fcrm_campaign( WP_REST_Request $request ) {
	// Only what the pack sends: no sender or reply-to (settings.mailer_settings), footer or if_exists, which
	// a person would later send without seeing.
	$input = wpbruecke_fcrm_input( $request, array( 'campaign_id', 'title', 'email_subject', 'email_pre_header', 'email_body', 'design_template', 'settings', 'recipients', 'exclude_recipients' ) );
	if ( isset( $input['design_template'] ) && ! in_array( $input['design_template'], array( 'simple', 'plain', 'classic' ), true ) ) {
		return new WP_Error( 'wpbruecke_fcrm_template', __( 'design_template is simple, plain or classic (block markup FluentCRM\'s editor keeps).', 'wpbruecke-connector' ), array( 'status' => 400 ) );
	}
	if ( isset( $input['settings'] ) ) {
		$input['settings'] = array_intersect_key( (array) $input['settings'], array_flip( array( 'template_config' ) ) );
	}
	if ( ! empty( $input['campaign_id'] ) ) {
		$found = wpbruecke_fcrm_campaign( $input['campaign_id'], true );
		if ( is_wp_error( $found ) ) {
			return $found;
		}
		$input['campaign_id'] = (int) $input['campaign_id'];
	}
	$response = wpbruecke_fcrm_lend( 'fluent-crm/upsert-campaign', $input );
	if ( is_wp_error( $response ) ) {
		return $response;
	}
	$data = (array) $response->get_data();
	$id   = isset( $data['campaign']['id'] ) ? (int) $data['campaign']['id'] : 0;
	if ( $id ) {
		$saved              = wpbruecke_fcrm_campaign( $id, false );
		$data['campaign_id'] = $id;
		$data['title']      = isset( $data['campaign']['title'] ) ? $data['campaign']['title'] : '';
		$data['created_by'] = is_wp_error( $saved ) ? null : (int) $saved->created_by;
		$data['editor_url'] = admin_url( 'admin.php?page=fluentcrm-admin#/email/campaigns/' . $id );
	}
	return rest_ensure_response( $data );
}

/**
 * The calling user's own contact before a render: found, relinked or recreated, its id kept in the
 * boxes. A WP_Error 409 when there is none (FluentCRM would fill SmartCodes from another contact).
 *
 * @return int|WP_Error Contact ID.
 */
function wpbruecke_fcrm_render_contact() {
	$user = wp_get_current_user();
	$id   = ( $user && $user->ID ) ? wpbruecke_fcrm_own_contact( $user ) : null;
	if ( ! $id ) {
		return wpbruecke_fcrm_no_own_contact();
	}
	$all = wpbruecke_fcrm_all_boxes();
	if ( isset( $all[ $user->ID ] ) && ( ! isset( $all[ $user->ID ]['contact'] ) || (int) $all[ $user->ID ]['contact'] !== $id ) ) {
		$all[ $user->ID ]['contact'] = $id;
		update_option( WPBRUECKE_FCRM_OPTION, $all, false );
	}
	return $id;
}

/**
 * The refusal when no render can use the Hermes user's own contact.
 *
 * @return WP_Error
 */
function wpbruecke_fcrm_no_own_contact() {
	return new WP_Error( 'wpbruecke_fcrm_no_own_contact', __( 'WPBrücke could not find or create the Hermes user\'s own FluentCRM contact, so nothing was rendered or sent: FluentCRM would fill SmartCodes from another contact, a real customer. The site\'s admin checks the user\'s email address and saves Settings > WPBrücke FluentCRM again.', 'wpbruecke-connector' ), array( 'status' => 409 ) );
}

/**
 * Whether FluentCRM rendered against the contact the route named (the answer's rendered_against, which
 * the lent fcrm_read_contacts makes visible).
 *
 * @param array $data    The ability's answer.
 * @param int   $contact Contact ID.
 * @return bool
 */
function wpbruecke_fcrm_rendered_for( array $data, $contact ) {
	return isset( $data['rendered_against']['contact_id'] ) && (int) $data['rendered_against']['contact_id'] === (int) $contact;
}

/**
 * POST fluentcrm/preview: the rendered email of a saved campaign (HTML and text), as FluentCRM renders it
 * against the Hermes user's own contact.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_fcrm_preview( WP_REST_Request $request ) {
	$input = wpbruecke_fcrm_input( $request, array( 'campaign_id' ) );
	$found = wpbruecke_fcrm_campaign( isset( $input['campaign_id'] ) ? $input['campaign_id'] : 0, false );
	if ( is_wp_error( $found ) ) {
		return $found;
	}
	$contact = wpbruecke_fcrm_render_contact();
	if ( is_wp_error( $contact ) ) {
		return $contact;
	}
	$response = wpbruecke_fcrm_lend(
		'fluent-crm/render-email-preview',
		array(
			'campaign_id'        => (int) $found->id,
			'body_format'        => 'both',
			'against_contact_id' => $contact,
		),
		array( WPBRUECKE_FCRM_MANAGE, 'fcrm_read_contacts' )
	);
	if ( is_wp_error( $response ) ) {
		return $response;
	}
	$data = (array) $response->get_data();
	if ( ! wpbruecke_fcrm_rendered_for( $data, $contact ) ) {
		return wpbruecke_fcrm_no_own_contact();
	}
	unset( $data['rendered_against'] );
	$data['rendered_for'] = 'the Hermes user\'s own FluentCRM contact';
	$data['campaign_id']  = (int) $found->id;
	$data['title']        = (string) $found->title;
	return rest_ensure_response( $data );
}

/**
 * POST fluentcrm/test: one test copy of a saved campaign to the address saved on the screen. An address
 * in the request is ignored.
 *
 * @param WP_REST_Request $request Request.
 * @return WP_REST_Response|WP_Error
 */
function wpbruecke_rest_fcrm_test( WP_REST_Request $request ) {
	$input = wpbruecke_fcrm_input( $request, array( 'campaign_id' ) );
	$found = wpbruecke_fcrm_campaign( isset( $input['campaign_id'] ) ? $input['campaign_id'] : 0, false );
	if ( is_wp_error( $found ) ) {
		return $found;
	}
	$contact = wpbruecke_fcrm_render_contact();
	if ( is_wp_error( $contact ) ) {
		return $contact;
	}
	$boxes    = wpbruecke_fcrm_boxes( get_current_user_id() );
	$response = wpbruecke_fcrm_lend(
		'fluent-crm/send-test-email',
		array(
			'campaign_id'        => (int) $found->id,
			'to_email'           => (string) $boxes['test_to'],
			'against_contact_id' => $contact,
		),
		array( WPBRUECKE_FCRM_MANAGE, 'fcrm_read_contacts' )
	);
	if ( is_wp_error( $response ) ) {
		return $response;
	}
	$data = (array) $response->get_data();
	if ( ! wpbruecke_fcrm_rendered_for( $data, $contact ) ) {
		return new WP_Error( 'wpbruecke_fcrm_no_own_contact', __( 'FluentCRM rendered the test email against a contact other than the Hermes user\'s own one (it was deleted during the call). Tell the person to check the test inbox and send the test again.', 'wpbruecke-connector' ), array( 'status' => 409 ) );
	}
	$data['campaign_id']  = (int) $found->id;
	$data['title']        = (string) $found->title;
	$data['to']           = 'the test address saved in WordPress';
	$data['rendered_for'] = 'the Hermes user\'s own FluentCRM contact';
	unset( $data['rendered_against'] );
	return rest_ensure_response( $data );
}

/**
 * The routes.
 */
function wpbruecke_fcrm_routes() {
	register_rest_route(
		'wpbruecke/v1',
		'/fluentcrm/permissions',
		array(
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => function () {
				return rest_ensure_response( wpbruecke_fcrm_permissions_for( get_current_user_id() ) );
			},
			'permission_callback' => 'wpbruecke_rest_can_edit_posts',
		)
	);
	$routes = array(
		'/fluentcrm/campaign' => array( 'wpbruecke_rest_fcrm_campaign', 'draft' ),
		'/fluentcrm/preview'  => array( 'wpbruecke_rest_fcrm_preview', 'draft' ),
		'/fluentcrm/test'     => array( 'wpbruecke_rest_fcrm_test', 'test_email' ),
	);
	foreach ( $routes as $route => $spec ) {
		$box = $spec[1];
		register_rest_route(
			'wpbruecke/v1',
			$route,
			array(
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => $spec[0],
				'permission_callback' => function () use ( $box ) {
					return wpbruecke_fcrm_box_open( $box );
				},
			)
		);
	}
}
add_action( 'rest_api_init', 'wpbruecke_fcrm_routes' );

/* ------------------------------------------------------------------ the screen */

/**
 * Settings > WPBrücke FluentCRM (administrators only).
 */
function wpbruecke_fcrm_menu() {
	add_options_page(
		__( 'WPBrücke FluentCRM', 'wpbruecke-connector' ),
		__( 'WPBrücke FluentCRM', 'wpbruecke-connector' ),
		'manage_options',
		WPBRUECKE_FCRM_PAGE,
		'wpbruecke_fcrm_render'
	);
}
add_action( 'admin_menu', 'wpbruecke_fcrm_menu' );

/**
 * Users the screen offers: they can edit posts and are no administrators.
 *
 * @return WP_User[]
 */
function wpbruecke_fcrm_candidates() {
	$users = get_users(
		array(
			'capability' => 'edit_posts',
			'number'     => 200,
			'orderby'    => 'login',
		)
	);
	return array_values( array_filter( $users, 'wpbruecke_fcrm_pickable' ) );
}

/**
 * The screen.
 */
function wpbruecke_fcrm_render() {
	if ( ! current_user_can( 'manage_options' ) ) {
		wp_die( esc_html__( 'Your WordPress user is not allowed to do this.', 'wpbruecke-connector' ) );
	}
	echo '<div class="wrap"><h1>' . esc_html__( 'WPBrücke FluentCRM', 'wpbruecke-connector' ) . '</h1>';
	echo '<p>' . esc_html__( 'What Hermes may do in FluentCRM, for the WordPress user Hermes works as on this site. Only an administrator can change it.', 'wpbruecke-connector' ) . '</p>';
	$notices = array(
		'saved'            => array( 'success', __( 'Saved.', 'wpbruecke-connector' ) ),
		'saved_no_address' => array( 'warning', __( 'Saved. "Send test emails" stays off until a valid address is saved.', 'wpbruecke-connector' ) ),
		'not_pickable'     => array( 'error', __( 'Pick the Hermes user: a user who can edit posts and is no administrator.', 'wpbruecke-connector' ) ),
	);
	$notice = isset( $_GET['wpb_notice'] ) ? sanitize_key( wp_unslash( $_GET['wpb_notice'] ) ) : ''; // phpcs:ignore WordPress.Security.NonceVerification.Recommended -- display only.
	if ( isset( $notices[ $notice ] ) ) {
		echo '<div class="notice notice-' . esc_attr( $notices[ $notice ][0] ) . '"><p>' . esc_html( $notices[ $notice ][1] ) . '</p></div>';
	}
	if ( ! wpbruecke_fcrm_ready() ) {
		echo '<p>' . esc_html__( 'FluentCRM is not active on this site.', 'wpbruecke-connector' ) . '</p></div>';
		return;
	}
	$candidates = wpbruecke_fcrm_candidates();
	$picked     = isset( $_GET['user'] ) ? absint( $_GET['user'] ) : 0; // phpcs:ignore WordPress.Security.NonceVerification.Recommended -- which user to show.
	if ( ! $picked ) {
		foreach ( $candidates as $c ) {
			if ( null !== wpbruecke_fcrm_boxes( $c->ID ) ) {
				$picked = $c->ID;
				break;
			}
		}
	}
	echo '<form method="get" action="' . esc_url( admin_url( 'options-general.php' ) ) . '">';
	echo '<input type="hidden" name="page" value="' . esc_attr( WPBRUECKE_FCRM_PAGE ) . '" />';
	echo '<label for="wpb-fcrm-user">' . esc_html__( 'Hermes user', 'wpbruecke-connector' ) . '</label> <select id="wpb-fcrm-user" name="user">';
	echo '<option value="0">' . esc_html__( 'Pick a user', 'wpbruecke-connector' ) . '</option>';
	foreach ( $candidates as $c ) {
		echo '<option value="' . esc_attr( (string) $c->ID ) . '"' . selected( $picked, $c->ID, false ) . '>' . esc_html( $c->display_name . ' (' . $c->user_login . ')' ) . '</option>';
	}
	echo '</select> ';
	submit_button( __( 'Show', 'wpbruecke-connector' ), 'secondary', '', false );
	echo '<p class="description">' . esc_html__( 'Administrators are never offered: FluentCRM gives them everything.', 'wpbruecke-connector' ) . '</p></form>';

	$user = $picked ? get_user_by( 'id', $picked ) : false;
	if ( ! wpbruecke_fcrm_pickable( $user ) ) {
		echo '</div>';
		return;
	}
	$boxes = wpbruecke_fcrm_boxes( $user->ID );
	$perms = wpbruecke_fcrm_stored_permissions( $user->ID );
	$holds = array(
		'view' => in_array( WPBRUECKE_FCRM_READ, $perms, true ),
		'send' => in_array( WPBRUECKE_FCRM_MANAGE, $perms, true ),
	);
	if ( null === $boxes ) {
		$checked = array( 'view' => true, 'draft' => true, 'test' => false, 'send' => $holds['send'] );
		echo '<div class="notice notice-info inline"><p>' . esc_html__( 'Not saved for this user yet: the boxes show the defaults, and Hermes does nothing in FluentCRM until you save.', 'wpbruecke-connector' ) . '</p></div>';
	} else {
		$checked = array( 'view' => $holds['view'], 'draft' => ! empty( $boxes['draft'] ), 'test' => ! empty( $boxes['test'] ), 'send' => $holds['send'] );
	}
	$now = function ( $held ) {
		return $held ? __( 'FluentCRM now: granted', 'wpbruecke-connector' ) : __( 'FluentCRM now: not granted', 'wpbruecke-connector' );
	};
	echo '<form method="post" action="' . esc_url( admin_url( 'admin-post.php' ) ) . '">';
	echo '<input type="hidden" name="action" value="wpbruecke_fcrm_save" />';
	echo '<input type="hidden" name="user" value="' . esc_attr( (string) $user->ID ) . '" />';
	wp_nonce_field( 'wpbruecke_fcrm_save_' . $user->ID );
	echo '<table class="form-table" role="presentation"><tbody>';
	$rows = array(
		'view'  => array( __( 'View campaigns', 'wpbruecke-connector' ), __( 'Grants FluentCRM\'s "Read emails" permission (fcrm_read_emails).', 'wpbruecke-connector' ) . ' ' . $now( $holds['view'] ) ),
		'draft' => array( __( 'Write draft campaigns', 'wpbruecke-connector' ), __( 'Hermes saves and previews draft campaigns through this connector; it cannot send them.', 'wpbruecke-connector' ) ),
		'test'  => array( __( 'Send test emails, to the address below', 'wpbruecke-connector' ), __( 'Test copies go only to the address saved here; an address Hermes names is ignored.', 'wpbruecke-connector' ) ),
		'send'  => array( __( 'Schedule and send campaigns', 'wpbruecke-connector' ), __( 'This grants FluentCRM\'s whole "Write and send emails" permission (fcrm_manage_emails), so anything that uses this user\'s password can send through FluentCRM; WPBrücke still asks with a card before each send it makes.', 'wpbruecke-connector' ) . ' ' . $now( $holds['send'] ) ),
	);
	foreach ( $rows as $key => $row ) {
		echo '<tr><th scope="row">' . esc_html( $row[0] ) . '</th><td><label><input type="checkbox" name="wpb_' . esc_attr( $key ) . '" value="1"' . checked( $checked[ $key ], true, false ) . ' /> ' . esc_html( $row[1] ) . '</label></td></tr>';
	}
	$address = isset( $boxes['test_to'] ) ? (string) $boxes['test_to'] : '';
	echo '<tr><th scope="row"><label for="wpb-fcrm-to">' . esc_html__( 'Test email address', 'wpbruecke-connector' ) . '</label></th><td><input type="email" class="regular-text" id="wpb-fcrm-to" name="wpb_test_to" value="' . esc_attr( $address ) . '" /></td></tr>';
	echo '</tbody></table>';
	submit_button( __( 'Save', 'wpbruecke-connector' ) );
	echo '</form>';
	echo '<p class="description">' . esc_html__( 'Saving also gives this user a FluentCRM contact of its own (status transactional, in no list), and every preview and test email renders against it (recreating it when it was deleted), so they never show a real customer\'s data.', 'wpbruecke-connector' ) . '</p>';
	echo '</div>';
}

/**
 * The screen's save (admin-post.php).
 */
function wpbruecke_fcrm_handle_save() {
	$user_id = isset( $_POST['user'] ) ? absint( $_POST['user'] ) : 0; // phpcs:ignore WordPress.Security.NonceVerification.Missing -- the nonce action names the user; checked below.
	if ( ! current_user_can( 'manage_options' ) ) {
		wp_die( esc_html__( 'Your WordPress user is not allowed to do this.', 'wpbruecke-connector' ), 403 );
	}
	check_admin_referer( 'wpbruecke_fcrm_save_' . $user_id );
	$code = 'not_pickable';
	if ( wpbruecke_fcrm_ready() ) {
		$code = wpbruecke_fcrm_save(
			$user_id,
			array(
				'view'  => ! empty( $_POST['wpb_view'] ),
				'draft' => ! empty( $_POST['wpb_draft'] ),
				'test'  => ! empty( $_POST['wpb_test'] ),
				'send'  => ! empty( $_POST['wpb_send'] ),
			),
			isset( $_POST['wpb_test_to'] ) ? sanitize_email( wp_unslash( $_POST['wpb_test_to'] ) ) : ''
		);
	}
	wp_safe_redirect(
		add_query_arg(
			array(
				'page'       => WPBRUECKE_FCRM_PAGE,
				'user'       => $user_id,
				'wpb_notice' => $code,
			),
			admin_url( 'options-general.php' )
		)
	);
	exit;
}
add_action( 'admin_post_wpbruecke_fcrm_save', 'wpbruecke_fcrm_handle_save' );
