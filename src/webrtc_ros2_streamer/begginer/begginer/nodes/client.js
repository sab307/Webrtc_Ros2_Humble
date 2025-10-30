let pc = null;
let dc = null;
let dcInterval = null;
let statsInterval = null;

// Metrics
let frameCount = 0;
let lastFrameTime = Date.now();

// Get DOM elements
const iceGatheringLog = document.getElementById('ice-gathering-state');
const iceConnectionLog = document.getElementById('ice-connection-state');
const signalingLog = document.getElementById('signaling-state');
const dataChannelLog = document.getElementById('data-channel');
const rttValue = document.getElementById('rtt-value');
const rttIndicator = document.getElementById('rtt-indicator');
const fpsValue = document.getElementById('fps-value');
const packetsValue = document.getElementById('packets-value');

// Set up buttons
document.getElementById('start').onclick = start;
document.getElementById('stop').onclick = stop;

// Update RTT display with quality indicator
function updateRTTDisplay(rtt) {
    if (!rttValue) return;
    
    rttValue.textContent = rtt.toFixed(0);
    
    // Update quality indicator
    if (!rttIndicator) return;
    
    rttIndicator.className = 'quality-indicator';
    if (rtt < 50) {
        rttIndicator.classList.add('quality-excellent');
    } else if (rtt < 100) {
        rttIndicator.classList.add('quality-good');
    } else if (rtt < 150) {
        rttIndicator.classList.add('quality-fair');
    } else if (rtt < 200) {
        rttIndicator.classList.add('quality-poor');
    } else {
        rttIndicator.classList.add('quality-bad');
    }
}

// Get WebRTC stats
async function updateStats() {
    if (!pc) return;

    try {
        const stats = await pc.getStats();
        
        stats.forEach(report => {
            if (report.type === 'inbound-rtp' && report.kind === 'video') {
                // Update packets received
                if (report.packetsReceived && packetsValue) {
                    packetsValue.textContent = report.packetsReceived.toLocaleString();
                }
                
                // Calculate FPS
                if (report.framesDecoded && fpsValue) {
                    const now = Date.now();
                    const elapsed = (now - lastFrameTime) / 1000;
                    if (elapsed > 1) {
                        const fps = (report.framesDecoded - frameCount) / elapsed;
                        fpsValue.textContent = fps.toFixed(1);
                        frameCount = report.framesDecoded;
                        lastFrameTime = now;
                    }
                }
            }
        });
    } catch (err) {
        console.error('Error getting stats:', err);
    }
}

function createPeerConnection() {
    const config = {
        iceServers: [
            {
                urls: [
                    'stun:stun.l.google.com:19302',
                    'stun:stun1.l.google.com:19302',
                ]
            }
        ],
        iceCandidatePoolSize: 10,
        bundlePolicy: 'max-bundle',
        rtcpMuxPolicy: 'require'
    };

    pc = new RTCPeerConnection(config);

    // ICE gathering state
    pc.addEventListener('icegatheringstatechange', () => {
        if (iceGatheringLog) {
            iceGatheringLog.textContent += ' -> ' + pc.iceGatheringState;
        }
        console.log('ICE Gathering State:', pc.iceGatheringState);
    });
    if (iceGatheringLog) {
        iceGatheringLog.textContent = pc.iceGatheringState;
    }

    // ICE connection state
    pc.addEventListener('iceconnectionstatechange', () => {
        if (iceConnectionLog) {
            iceConnectionLog.textContent += ' -> ' + pc.iceConnectionState;
        }
        console.log('ICE Connection State:', pc.iceConnectionState);
        
        if (pc.iceConnectionState === 'connected') {
            console.log('✓ Stream connected and active');
            statsInterval = setInterval(updateStats, 1000);
        }
        
        if (pc.iceConnectionState === 'failed') {
            console.error('ICE connection failed');
            clearInterval(statsInterval);
            alert('Connection failed. Please check your camera is publishing and try again.');
        }
        
        if (pc.iceConnectionState === 'disconnected') {
            console.warn('ICE connection disconnected');
        }
        
        if (pc.iceConnectionState === 'closed') {
            console.log('ICE connection closed');
            clearInterval(statsInterval);
        }
    });
    if (iceConnectionLog) {
        iceConnectionLog.textContent = pc.iceConnectionState;
    }

    // Signaling state
    pc.addEventListener('signalingstatechange', () => {
        if (signalingLog) {
            signalingLog.textContent += ' -> ' + pc.signalingState;
        }
        console.log('Signaling State:', pc.signalingState);
    });
    if (signalingLog) {
        signalingLog.textContent = pc.signalingState;
    }

    // Handle incoming tracks
    pc.addEventListener('track', (evt) => {
        console.log('✓ Received track:', evt.track.kind);
        const video = document.getElementById('video');
        
        if (!video) {
            console.error('Video element not found!');
            return;
        }
        
        // Monitor track state
        evt.track.addEventListener('ended', () => {
            console.error('❌ Track ended unexpectedly!');
        });
        
        evt.track.addEventListener('mute', () => {
            console.warn('Track muted');
        });
        
        evt.track.addEventListener('unmute', () => {
            console.log('Track unmuted');
        });
        
        if (evt.streams && evt.streams[0]) {
            video.srcObject = evt.streams[0];
            console.log('✓ Video stream attached');
        } else {
            if (!video.srcObject) {
                const stream = new MediaStream();
                video.srcObject = stream;
            }
            video.srcObject.addTrack(evt.track);
            console.log('✓ Video track added to stream');
        }
    });

    // Log ICE candidates
    pc.addEventListener('icecandidate', (evt) => {
        if (evt.candidate) {
            console.log('Local ICE candidate:', evt.candidate.candidate);
        }
    });

    return pc;
}

async function negotiate() {
    try {
        console.log('Creating offer...');
        const offer = await pc.createOffer({
            offerToReceiveVideo: true,
            offerToReceiveAudio: false
        });
        
        await pc.setLocalDescription(offer);
        console.log('✓ Local description set');

        // Wait for ICE gathering
        await new Promise((resolve) => {
            if (pc.iceGatheringState === 'complete') {
                resolve();
            } else {
                const checkState = () => {
                    if (pc.iceGatheringState === 'complete') {
                        pc.removeEventListener('icegatheringstatechange', checkState);
                        resolve();
                    }
                };
                pc.addEventListener('icegatheringstatechange', checkState);
            }
        });

        console.log('Sending offer to server...');
        const response = await fetch('/offer', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                sdp: pc.localDescription.sdp,
                type: pc.localDescription.type,
            }),
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const answer = await response.json();
        console.log('✓ Received answer from server');
        
        await pc.setRemoteDescription(answer);
        console.log('✓ Remote description set');
        console.log('✓ Connection established, waiting for video...');
        
    } catch (err) {
        console.error('❌ Negotiation failed:', err);
        alert('Failed to connect: ' + err.message);
    }
}

async function start() {
    console.log('Starting stream...');
    
    // Reset metrics
    frameCount = 0;
    lastFrameTime = Date.now();
    if (rttValue) rttValue.textContent = '--';
    if (fpsValue) fpsValue.textContent = '--';
    if (packetsValue) packetsValue.textContent = '--';
    
    pc = createPeerConnection();

    // Create data channel
    dc = pc.createDataChannel('chat', { ordered: true });
    
    dc.addEventListener('close', () => {
        clearInterval(dcInterval);
        clearInterval(statsInterval);
        if (dataChannelLog) {
            dataChannelLog.textContent += ' -> closed';
        }
        console.log('Data channel closed');
    });

    dc.addEventListener('open', () => {
        if (dataChannelLog) {
            dataChannelLog.textContent += ' -> open';
        }
        console.log('✓ Data channel opened');
        
        // Send ping every 500ms for RTT measurement
        dcInterval = setInterval(() => {
            if (dc.readyState === 'open') {
                const message = JSON.stringify({
                    type: 'ping',
                    timestamp: Date.now()
                });
                dc.send(message);
            }
        }, 500);
    });

    dc.addEventListener('message', (evt) => {
        try {
            const data = JSON.parse(evt.data);
            if (data.type === 'pong') {
                const rtt = Date.now() - data.timestamp;
                updateRTTDisplay(rtt);
                
                // Send RTT back to server for adaptive streaming
                if (dc.readyState === 'open') {
                    dc.send(JSON.stringify({ rtt: rtt }));
                }
            }
        } catch (err) {
            console.error('Error parsing message:', err);
        }
    });

    if (dataChannelLog) {
        dataChannelLog.textContent = 'connecting';
    }

    // Start negotiation
    await negotiate();
}

function stop() {
    console.log('Stopping stream...');
    
    // Stop intervals
    if (dcInterval) {
        clearInterval(dcInterval);
    }
    if (statsInterval) {
        clearInterval(statsInterval);
    }
    
    // Stop data channel
    if (dc) {
        dc.close();
    }

    // Close peer connection
    if (pc) {
        pc.close();
        pc = null;
    }

    // Clear video
    const video = document.getElementById('video');
    if (video && video.srcObject) {
        video.srcObject.getTracks().forEach(track => track.stop());
        video.srcObject = null;
    }

    // Reset logs
    if (iceGatheringLog) iceGatheringLog.textContent = 'new';
    if (iceConnectionLog) iceConnectionLog.textContent = 'new';
    if (signalingLog) signalingLog.textContent = 'stable';
    if (dataChannelLog) dataChannelLog.textContent = 'closed';
    
    // Reset metrics
    if (rttValue) rttValue.textContent = '--';
    if (fpsValue) fpsValue.textContent = '--';
    if (packetsValue) packetsValue.textContent = '--';
    if (rttIndicator) rttIndicator.className = 'quality-indicator';
    
    console.log('✓ Stream stopped');
}

console.log('✓ WebRTC client loaded successfully');
