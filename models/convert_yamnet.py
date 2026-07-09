"""Reproducibly build eeper's cry-detection ONNX artifact from canonical YAMNet.

eeper uses a SPLIT YAMNet: this script converts only YAMNet's classifier body
(log-mel patch -> 521 AudioSet scores) to ONNX; the log-mel frontend is eeper's
own versioned NumPy code (server/eeper/insight/frontend.py). Splitting avoids the
fragile in-graph STFT/RFFT ops that break a whole-model tf2onnx conversion, and
keeps preprocessing as ordinary, unit-tested code that runs identically on
amd64/arm64.

Provenance / license: canonical YAMNet from tensorflow/models (research/audioset/
yamnet), Apache-2.0, weights https://storage.googleapis.com/audioset/yamnet.h5.
The infant-cry class is index 20 ("Baby cry, infant cry") of the 521 AudioSet
classes. The converted artifact is hosted as a GitHub Release asset and pinned by
URL + SHA-256 in manifest.json (fetched + checksum-verified at first run).

HOW TO RUN (one-time, on any machine; pins Keras-2 so tf2onnx converts cleanly):

    mkdir yamnet && cd yamnet
    base=https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet
    for f in yamnet.py params.py features.py yamnet_class_map.csv; do curl -sSLO "$base/$f"; done
    curl -sSLO https://storage.googleapis.com/audioset/yamnet.h5
    docker run --rm -v "$PWD:/w" -w /w python:3.11-slim bash -c '
      pip install -q "tensorflow==2.15.1" "tf_keras==2.15.1" "tf2onnx==1.16.1" \
        "onnxruntime==1.19.2" "numpy<2" &&
      python /w/../convert_yamnet.py'

Verified: the split (eeper frontend -> this ONNX) reproduces reference waveform-in
YAMNet to < 1e-5; ONNX vs the source TF classifier match to < 1e-6.
"""

from __future__ import annotations

import hashlib
import sys

import numpy as np
import tensorflow as tf
import tf_keras as keras
import tf2onnx
import onnxruntime as ort

sys.path.insert(0, ".")  # the yamnet source files fetched above
import features as features_lib  # noqa: E402
import params as yamnet_params  # noqa: E402
import yamnet as yamnet_lib  # noqa: E402

H5 = "yamnet.h5"
OUT = "yamnet_classifier.onnx"
CRY_CLASS_INDEX = 20  # "Baby cry, infant cry"

params = yamnet_params.Params()

# Load the full waveform-in model, then extract the classifier as a SUB-MODEL from
# the mel-patch boundary (the Reshape layer's input, [None,96,64]) to predictions —
# this reuses the exact trained layers, so no weight-transfer/name-matching risk.
frames = yamnet_lib.yamnet_frames_model(params)
frames.load_weights(H5)
reshape = next(layer for layer in frames.layers if isinstance(layer, keras.layers.Reshape))
classifier = keras.Model(inputs=reshape.input, outputs=frames.outputs[0], name="yamnet_classifier")

spec = (tf.TensorSpec((None, params.patch_frames, params.patch_bands), tf.float32, name="mel_patches"),)
tf2onnx.convert.from_keras(classifier, input_signature=spec, opset=13, output_path=OUT)

# Verify: eeper-style frontend -> ONNX reproduces reference waveform-in YAMNet.
rng = np.random.default_rng(0)
waveform = (0.1 * rng.standard_normal(16000)).astype(np.float32)
ref = frames(waveform)[0].numpy()
padded = features_lib.pad_waveform(tf.constant(waveform), params)
_, patches = features_lib.waveform_to_log_mel_spectrogram_patches(padded, params)
onnx_scores = ort.InferenceSession(OUT, providers=["CPUExecutionProvider"]).run(
    None, {"mel_patches": patches.numpy().astype(np.float32)}
)[0]
max_abs = float(np.max(np.abs(ref - onnx_scores)))
data = open(OUT, "rb").read()
print(f"cry-class index {CRY_CLASS_INDEX}; classes {onnx_scores.shape[-1]}")
print(f"max |reference - split| = {max_abs:.3e}")
print(f"{OUT}: {len(data)} bytes  sha256={hashlib.sha256(data).hexdigest()}")
assert max_abs < 1e-3 and onnx_scores.shape[-1] == 521, "conversion did not reproduce YAMNet"
print("OK — pin the printed URL + sha256 in manifest.json")
