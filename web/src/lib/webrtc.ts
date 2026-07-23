// Browser side of the WebRTC live path. The api relays signaling only
// (POST /api/v1/cameras/{id}/webrtc, offer -> answer); media flows directly
// between the browser and go2rtc's published media port over ICE.
import { api } from '$lib/api';

export type LiveSession = {
  pc: RTCPeerConnection;
  stream: MediaStream;
};

function iceGatheringComplete(pc: RTCPeerConnection, timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    if (pc.iceGatheringState === 'complete') {
      resolve();
      return;
    }
    const check = (): void => {
      if (pc.iceGatheringState === 'complete') {
        pc.removeEventListener('icegatheringstatechange', check);
        resolve();
      }
    };
    pc.addEventListener('icegatheringstatechange', check);
    // Non-trickle: our signaling is a single request/response, so wait for
    // gathering — but cap it so a stalled gather can't hang the connect.
    setTimeout(resolve, timeoutMs);
  });
}

/** Negotiate a recv-only WebRTC session for a camera. The caller attaches
 * `session.stream` to the <video> only after confirming the session is still the
 * current one — the negotiation is async, so the component may have moved on. */
export async function connectCamera(cameraId: number): Promise<LiveSession> {
  const pc = new RTCPeerConnection();
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });

  // Tracks land in this stream as they arrive; the same object updates the
  // <video> live once the caller attaches it.
  const stream = new MediaStream();
  pc.addEventListener('track', (event: RTCTrackEvent) => {
    stream.addTrack(event.track);
  });

  await pc.setLocalDescription(await pc.createOffer());
  await iceGatheringComplete(pc, 1500);

  const offer = pc.localDescription?.sdp;
  if (!offer) {
    pc.close();
    throw new Error('failed to create an offer');
  }

  const res = await api(`/cameras/${cameraId}/webrtc`, {
    method: 'POST',
    headers: { 'content-type': 'application/sdp' },
    body: offer,
  });
  if (!res.ok) {
    pc.close();
    throw new Error(`stream is not available (${res.status})`);
  }

  await pc.setRemoteDescription({ type: 'answer', sdp: await res.text() });
  return { pc, stream };
}

/** Negotiate a recv-only, AUDIO-ONLY session for the standalone host microphone
 * (the `mic` go2rtc stream, relayed via POST /api/v1/audio/webrtc). Used by the
 * "listen to the room" control, which works with no camera selected. */
export async function connectMic(): Promise<LiveSession> {
  const pc = new RTCPeerConnection();
  pc.addTransceiver('audio', { direction: 'recvonly' });

  const stream = new MediaStream();
  pc.addEventListener('track', (event: RTCTrackEvent) => {
    stream.addTrack(event.track);
  });

  await pc.setLocalDescription(await pc.createOffer());
  await iceGatheringComplete(pc, 1500);

  const offer = pc.localDescription?.sdp;
  if (!offer) {
    pc.close();
    throw new Error('failed to create an offer');
  }

  const res = await api('/audio/webrtc', {
    method: 'POST',
    headers: { 'content-type': 'application/sdp' },
    body: offer,
  });
  if (!res.ok) {
    pc.close();
    throw new Error(`microphone is not available (${res.status})`);
  }

  await pc.setRemoteDescription({ type: 'answer', sdp: await res.text() });
  return { pc, stream };
}

export type InboundVideoStats = {
  // Total decoded video frames — the reliable "frames are flowing" signal
  // (increments headless, no display needed).
  framesDecoded: number;
  // Average time a frame waits in the jitter buffer before playout (ms) — the
  // dominant, controllable latency component on a LAN. null until it populates.
  jitterBufferMs: number | null;
};

// Structural type so we don't depend on the exact lib.dom version exposing these.
type InboundRtp = RTCStats & {
  kind?: string;
  framesDecoded?: number;
  jitterBufferDelay?: number;
  jitterBufferEmittedCount?: number;
  packetsReceived?: number;
};

export type InboundAudioStats = {
  // Whether an inbound audio RTP report exists at all — distinguishes "no audio
  // track negotiated" from "track present but no packets yet".
  hasTrack: boolean;
  // Total received audio RTP packets — the "audio is flowing" signal. Increments
  // even while the <video> is muted (mute is a local playout control only).
  packetsReceived: number;
};

export async function inboundVideoStats(pc: RTCPeerConnection): Promise<InboundVideoStats> {
  let framesDecoded = 0;
  let jitterBufferMs: number | null = null;
  const stats = await pc.getStats();
  stats.forEach((report) => {
    if (report.type !== 'inbound-rtp') return;
    const inbound = report as InboundRtp;
    if (inbound.kind !== 'video') return;
    framesDecoded = inbound.framesDecoded ?? 0;
    const delay = inbound.jitterBufferDelay;
    const count = inbound.jitterBufferEmittedCount;
    if (typeof delay === 'number' && typeof count === 'number' && count > 0) {
      jitterBufferMs = (delay / count) * 1000;
    }
  });
  return { framesDecoded, jitterBufferMs };
}

export async function inboundAudioStats(pc: RTCPeerConnection): Promise<InboundAudioStats> {
  let hasTrack = false;
  let packetsReceived = 0;
  const stats = await pc.getStats();
  stats.forEach((report) => {
    if (report.type !== 'inbound-rtp') return;
    const inbound = report as InboundRtp;
    if (inbound.kind !== 'audio') return;
    hasTrack = true;
    packetsReceived = inbound.packetsReceived ?? 0;
  });
  return { hasTrack, packetsReceived };
}
