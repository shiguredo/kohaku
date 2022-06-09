-- https://www.w3.org/TR/webrtc-stats/#dom-rtcstats

CREATE EXTENSION IF NOT EXISTS timescaledb;

DROP TABLE IF EXISTS sora_connection;
CREATE TABLE IF NOT EXISTS sora_connection (
    id bigserial NOT NULL PRIMARY KEY,

    -- クライアント側から送られてきたタイムスタンプ
    timestamp timestamptz NOT NULL,

    version varchar(255) NOT NULL,
    label varchar(255) NOT NULL,
    node_name varchar(255) NOT NULL,

    multistream boolean NOT NULL,
    simulcast boolean NOT NULL,
    spotlight boolean NOT NULL,

    role char(8) NOT NULL,
    channel_id varchar(255) NOT NULL,
    session_id char(26) NOT NULL,
    client_id varchar(255) NOT NULL,
    connection_id char(26) NOT NULL,

    -- TODO(v): audio? video?

    created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL
);

DROP TABLE IF EXISTS rtc_codec_stats;
CREATE TABLE IF NOT EXISTS rtc_codec_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    payload_type bigint NOT NULL,
    codec_type varchar(255) NULL,

    -- required だが Safari では codec に含まれていないため NULL を許容する
    transport_id varchar(255) NULL,
    -- 仕様と現実が違う問題がありそう
    mime_type varchar(255) NOT NULL,
    clock_rate bigint NULL,
    channels bigint NULL,
    sdp_fmtp_line varchar(255) NULL
);
SELECT create_hypertable('rtc_codec_stats', 'time');
ALTER TABLE rtc_codec_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_codec_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_inbound_rtp_stream_stats;
CREATE TABLE IF NOT EXISTS rtc_inbound_rtp_stream_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    ssrc bigint NOT NULL,
    kind varchar(255) NOT NULL,
    transport_id varchar(255) NULL,
    codec_id varchar(255) NULL,

    packets_received numeric NULL,
    packets_lost bigint NULL,
    jitter double precision NULL,
    packets_discarded numeric NULL,
    packets_repaired numeric NULL,
    burst_packets_lost numeric NULL,
    burst_packets_discarded numeric NULL,
    burst_loss_count bigint NULL,
    burst_discard_count bigint NULL,
    burst_loss_rate double precision NULL,
    burst_discard_rate double precision NULL,
    gap_loss_rate double precision NULL,
    gap_discard_rate double precision NULL,
    frames_dropped bigint NULL,
    partial_frames_lost bigint NULL,
    full_frames_lost bigint NULL,

    -- required だが inbound-rtp に含まれていないため NULL を許容する
    receiver_id varchar(255) NULL,
    remote_id varchar(255) NULL,
    frames_decoded bigint NULL,
    key_frames_decoded bigint NULL,
    frame_width bigint NULL,
    frame_height bigint NULL,
    frame_bit_depth bigint NULL,
    frames_per_second double precision NULL,
    qp_sum numeric NULL,

    total_decode_time double precision NULL,
    total_inter_frame_delay double precision NULL,
    total_squared_inter_frame_delay double precision NULL,
    voice_activity_flag boolean NULL,
    last_packet_received_timestamp double precision NULL,
    average_rtcp_interval double precision NULL,
    header_bytes_received numeric NULL,
    fec_packets_received numeric NULL,
    fec_packets_discarded numeric NULL,
    bytes_received numeric NULL,
    packets_failed_decryption numeric NULL,
    packets_duplicated numeric NULL,
    per_dscp_packets_received varchar(255) NULL,
    nack_count bigint NULL,
    fir_count bigint NULL,
    pli_count bigint NULL,
    sli_count bigint NULL,
    total_processing_delay double precision NULL,
    estimated_playout_timestamp double precision NULL,
    jitter_buffer_delay double precision NULL,
    jitter_buffer_emitted_count numeric NULL,
    total_samples_received numeric NULL,
    total_samples_decoded numeric NULL,
    samples_decoded_with_silk numeric NULL,
    samples_decoded_with_celt numeric NULL,
    concealed_samples numeric NULL,
    silent_concealed_samples numeric NULL,
    concealment_events numeric NULL,
    inserted_samples_for_deceleration numeric NULL,
    removed_samples_for_acceleration numeric NULL,
    audio_level double precision NULL,
    total_audio_energy double precision NULL,
    total_samples_duration double precision NULL,
    frames_received bigint NULL,
    decoder_implementation varchar(255) NULL
);
SELECT create_hypertable('rtc_inbound_rtp_stream_stats', 'time');
ALTER TABLE rtc_inbound_rtp_stream_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_inbound_rtp_stream_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_remote_inbound_rtp_stream_stats;
CREATE TABLE IF NOT EXISTS rtc_remote_inbound_rtp_stream_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    ssrc bigint NOT NULL,
    kind varchar(255) NOT NULL,
    transport_id varchar(255) NULL,
    codec_id varchar(255) NULL,

    packets_received numeric NULL,
    packets_lost bigint NULL,
    jitter double precision NULL,
    packets_discarded numeric NULL,
    packets_repaired numeric NULL,
    burst_packets_lost numeric NULL,
    burst_packets_discarded numeric NULL,
    burst_loss_count bigint NULL,
    burst_discard_count bigint NULL,
    burst_loss_rate double precision NULL,
    burst_discard_rate double precision NULL,
    gap_loss_rate double precision NULL,
    gap_discard_rate double precision NULL,
    frames_dropped bigint NULL,
    partial_frames_lost bigint NULL,
    full_frames_lost bigint NULL,

    local_id varchar(255) NULL,
    round_trip_time double precision NULL,
    total_round_trip_time double precision NULL,
    fraction_lost double precision NULL,
    reports_received numeric NULL,
    round_trip_time_measurements numeric NULL
);
SELECT create_hypertable('rtc_remote_inbound_rtp_stream_stats', 'time');
ALTER TABLE rtc_remote_inbound_rtp_stream_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_remote_inbound_rtp_stream_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_outbound_rtp_stream_stats;
CREATE TABLE IF NOT EXISTS rtc_outbound_rtp_stream_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    -- RTCRtpStreamStats
    ssrc bigint NOT NULL,
    kind varchar(255) NOT NULL,
    transport_id varchar(255) NULL,
    codec_id varchar(255) NULL,

    -- RTCSentRtpStreamStats
    packets_sent bigint NULL,
    bytes_sent numeric NULL,

    rtx_ssrc bigint NULL,
    media_source_id varchar(255) NULL,
    sender_id varchar(255) NULL,
    remote_id varchar(255) NULL,
    rid varchar(255) NULL,
    last_packet_sent_timestamp double precision NULL,
    header_bytes_sent numeric NULL,
    packets_discarded_on_send bigint NULL,
    bytes_discarded_on_send numeric NULL,
    fec_packets_sent bigint NULL,
    retransmitted_packets_sent numeric NULL,
    retransmitted_bytes_sent numeric NULL,
    target_bitrate double precision NULL,
    total_encoded_bytes_target numeric NULL,
    frame_width bigint NULL,
    frame_height bigint NULL,
    frame_bit_depth bigint NULL,
    frames_per_second double precision NULL,
    frames_sent bigint NULL,
    huge_frames_sent bigint NULL,
    frames_encoded bigint NULL,
    key_frames_encoded bigint NULL,
    frames_discarded_on_send bigint NULL,
    qp_sum numeric NULL,
    total_samples_sent numeric NULL,
    samples_encoded_with_silk numeric NULL,
    samples_encoded_with_celt numeric NULL,
    voice_activity_flag boolean NULL,
    total_encode_time double precision NULL,
    total_packet_send_delay double precision NULL,
    average_rtcp_interval double precision NULL,
    quality_limitation_reason varchar(9) NULL,
    quality_limitation_durations varchar(255) NULL,
    quality_limitation_resolution_changes bigint NULL,
    per_dscp_packets_sent varchar(255) NULL,
    nack_count bigint NULL,
    fir_count bigint NULL,
    pli_count bigint NULL,
    sli_count bigint NULL,
    encoder_implementation varchar(255) NULL
);
SELECT create_hypertable('rtc_outbound_rtp_stream_stats', 'time');
ALTER TABLE rtc_outbound_rtp_stream_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_outbound_rtp_stream_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_remote_outbound_rtp_stream_stats;
CREATE TABLE IF NOT EXISTS rtc_remote_outbound_rtp_stream_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    -- RTCRtpStreamStats
    ssrc bigint NOT NULL,
    kind varchar(255) NOT NULL,
    transport_id varchar(255) NULL,
    codec_id varchar(255) NULL,

    -- RTCSentRtpStreamStats
    packets_sent bigint NULL,
    bytes_sent numeric NULL,

    local_id varchar(255) NULL,
    remote_timestamp double precision NULL,
    reports_sent numeric NULL,
    round_trip_time double precision NULL,
    total_round_trip_time double precision NULL,
    round_trip_time_measurements numeric NULL
);
SELECT create_hypertable('rtc_remote_outbound_rtp_stream_stats', 'time');
ALTER TABLE rtc_remote_outbound_rtp_stream_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_remote_outbound_rtp_stream_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_audio_source_stats;
CREATE TABLE IF NOT EXISTS rtc_audio_source_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    track_identifier varchar(255) NOT NULL,
    kind varchar(255) NOT NULL,
    relayed_source boolean NULL,

    audio_level double precision NULL,
    total_audio_energy double precision NULL,
    total_samples_duration double precision NULL,
    echo_return_loss double precision NULL,
    echo_return_loss_enhancement double precision NULL
);
SELECT create_hypertable('rtc_audio_source_stats', 'time');
ALTER TABLE rtc_audio_source_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_audio_source_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_video_source_stats;
CREATE TABLE IF NOT EXISTS rtc_video_source_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    track_identifier varchar(255) NOT NULL,
    kind varchar(255) NOT NULL,
    relayed_source boolean NULL,

    width bigint NULL,
    height bigint NULL,
    bit_depth bigint NULL,
    frames bigint NULL,
    frames_per_second double precision NULL
);
SELECT create_hypertable('rtc_video_source_stats', 'time');
ALTER TABLE rtc_video_source_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_video_source_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_data_channel_stats;
CREATE TABLE IF NOT EXISTS rtc_data_channel_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    label varchar(255) NULL,
    protocol varchar(255) NULL,
    data_channel_identifier integer NULL,
    state varchar(255) NOT NULL,
    messages_sent bigint NULL,
    bytes_sent numeric NULL,
    messages_received bigint NULL,
    bytes_received numeric NULL
);
SELECT create_hypertable('rtc_data_channel_stats', 'time');
ALTER TABLE rtc_data_channel_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_data_channel_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_transport_stats;
CREATE TABLE IF NOT EXISTS rtc_transport_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    packets_sent numeric NULL,
    packets_received numeric NULL,
    bytes_sent numeric NULL,
    bytes_received numeric NULL,
    rtcp_transport_stats_id char(255) NULL,
    ice_role varchar(255) NULL,
    ice_local_username_fragment varchar(255) NULL,
    dtls_state varchar(255) NOT NULL,
    ice_state varchar(255) NULL,
    selected_candidate_pair_id char(255) NULL,
    local_certificate_id varchar(255) NULL,
    remote_certificate_id varchar(255) NULL,
    tls_version varchar(255) NULL,
    dtls_cipher varchar(255) NULL,
    srtp_cipher varchar(255) NULL,
    tls_group varchar(255) NULL,
    selected_candidate_pair_changes bigint NULL
);
SELECT create_hypertable('rtc_transport_stats', 'time');
ALTER TABLE rtc_transport_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_transport_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_ice_candidate_pair_stats;
CREATE TABLE IF NOT EXISTS rtc_ice_candidate_pair_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    transport_id varchar(255) NOT NULL,
    local_candidate_id varchar(255) NOT NULL,
    remote_candidate_id varchar(255) NOT NULL,
    state varchar(255) NOT NULL,
    nominated boolean NULL,
    packets_sent numeric NULL,
    packets_received numeric NULL,
    bytes_sent numeric NULL,
    bytes_received numeric NULL,
    last_packet_sent_timestamp double precision NULL,
    last_packet_received_timestamp double precision NULL,
    first_request_timestamp double precision NULL,
    last_request_timestamp double precision NULL,
    last_response_timestamp double precision NULL,
    total_round_trip_time double precision NULL,
    current_round_trip_time double precision NULL,
    available_outgoing_bitrate double precision NULL,
    available_incoming_bitrate double precision NULL,
    circuit_breaker_trigger_count bigint NULL,
    requests_received numeric NULL,
    requests_sent numeric NULL,
    responses_received numeric NULL,
    responses_sent numeric NULL,
    retransmissions_received numeric NULL,
    retransmissions_sent numeric NULL,
    consent_requests_sent numeric NULL,
    consent_expired_timestamp double precision NULL,
    packets_discarded_on_send bigint NULL,
    bytes_discarded_on_send numeric NULL,
    request_bytes_sent numeric NULL,
    consent_request_bytes_sent numeric NULL,
    response_bytes_sent numeric NULL
);
SELECT create_hypertable('rtc_ice_candidate_pair_stats', 'time');
ALTER TABLE rtc_ice_candidate_pair_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_ice_candidate_pair_stats', INTERVAL '3 days');

DROP TABLE IF EXISTS rtc_ice_candidate_stats;
CREATE TABLE IF NOT EXISTS rtc_ice_candidate_stats (
    time timestamptz NOT NULL,

    sora_connection_id char(26) NOT NULL,

    stats_timestamp double precision NOT NULL,
    stats_type varchar(255) NOT NULL,
    stats_id varchar(255) NOT NULL,

    -- required だが Firefox では local-candidate, remote-candidate に含まれていないため NULL を許容する
    transport_id varchar(255) NULL,
    address varchar(255) NULL,
    port integer NULL,
    protocol varchar(255) NULL,
    candidate_type varchar(255) NOT NULL,
    priority integer NULL,
    url varchar(255) NULL,
    relay_protocol varchar(255) NULL
);
SELECT create_hypertable('rtc_ice_candidate_stats', 'time');
ALTER TABLE rtc_ice_candidate_stats SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'sora_connection_id'
);
SELECT add_compression_policy('rtc_ice_candidate_stats', INTERVAL '3 days');