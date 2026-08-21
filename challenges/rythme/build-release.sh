#!/bin/bash
# Build the exact black-box player handout from a clean, pinned osu!lazer
# checkout. The issued archive contains the Windows client, operating
# instructions, and required notices only.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PINNED_OSU_COMMIT="0f3d3c3be19b134779879b3f18fe315da035c1ac"
PINNED_FRAMEWORK_COMMIT="0c3fb255384f24804e4797a31a656b340cb641d6"
NAME="reversing_rythme"
OUT="$ROOT/release/$NAME.zip"
LEGACY_DIRECT_EXE="$ROOT/release/Rythme.exe"
NATIVE_CRATE="$ROOT/src/client/native/rythme-native"
NATIVE_TARGET="x86_64-pc-windows-gnu"
RUST_TOOLCHAIN="1.96.0"

STAGE="$(mktemp -d)"
RELEASE_STAGE=""
cleanup() {
  rm -rf "$STAGE"
  if [ -n "$RELEASE_STAGE" ]; then
    rm -rf "$RELEASE_STAGE"
  fi
}
trap cleanup EXIT

if ! command -v cargo >/dev/null 2>&1; then
  echo "error: cargo is required to build the native client policy" >&2
  exit 1
fi
if ! command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
  echo "error: x86_64-w64-mingw32-gcc is required to build the native client policy" >&2
  exit 1
fi
if ! rustup run "$RUST_TOOLCHAIN" rustc --version >/dev/null 2>&1; then
  echo "error: Rust $RUST_TOOLCHAIN is required to build the native client policy" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required to verify release-generated policy constants" >&2
  exit 1
fi

echo "[*] verifying release-coupled protocol constants"
python3 "$ROOT/src/shared/policygen.py" --check

echo "[*] testing the production coordinator and lobby auto-start policy"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$ROOT/src/client/lazer-fork/tools/lobbycheck/lobbycheck.csproj" \
  --configuration Release

echo "[*] testing the persistent beatmap pool cache"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$ROOT/src/client/lazer-fork/tools/cachecheck/cachecheck.csproj" \
  --configuration Release

echo "[*] testing secure request interruption recovery"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$ROOT/src/client/lazer-fork/tools/transportcheck/transportcheck.csproj" \
  --configuration Release

echo "[*] testing coordinator payload and post-run receipt boundaries"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$ROOT/src/client/lazer-fork/tools/protocolcheck/protocolcheck.csproj" \
  --configuration Release

echo "[*] testing late-cue input recovery"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$ROOT/src/client/lazer-fork/tools/inputcheck/inputcheck.csproj" \
  --configuration Release

echo "[*] testing bounded end-of-song finalization"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$ROOT/src/client/lazer-fork/tools/finalizationcheck/finalizationcheck.csproj" \
  --configuration Release

NATIVE_TARGET_DIR="$STAGE/native-target"
echo "[*] building the pinned Rust native policy DLL"
rustup target add --toolchain "$RUST_TOOLCHAIN" "$NATIVE_TARGET"
CARGO_TARGET_DIR="$NATIVE_TARGET_DIR" \
RUSTFLAGS="--remap-path-prefix=$ROOT=Rythme" \
  cargo "+$RUST_TOOLCHAIN" build \
    --manifest-path "$NATIVE_CRATE/Cargo.toml" \
    --locked \
    --release \
    --target "$NATIVE_TARGET"
NATIVE_DLL="$NATIVE_TARGET_DIR/$NATIVE_TARGET/release/rythme_native.dll"
if ! file "$NATIVE_DLL" | grep -q 'PE32+ executable (DLL).*x86-64'; then
  echo "error: Rust build did not produce the expected Windows x86-64 DLL" >&2
  exit 1
fi
NATIVE_EXPORTS="$(x86_64-w64-mingw32-objdump -p "$NATIVE_DLL" | sed -n '/\[Ordinal\/Name Pointer\] Table/,+20p')"
NATIVE_EXPORT_COUNT="$(printf '%s\n' "$NATIVE_EXPORTS" | grep -Ec '^[[:space:]]+\[[[:space:]]*[0-9]+\]')"
if [ "$NATIVE_EXPORT_COUNT" -ne 1 ] || ! printf '%s\n' "$NATIVE_EXPORTS" | grep -Eq '] r4_gate$'; then
  echo "error: native policy must expose only the opaque r4_gate dispatcher" >&2
  exit 1
fi
NATIVE_DIGEST="$(sha256sum "$NATIVE_DLL" | cut -d' ' -f1)"
python3 "$ROOT/src/shared/policygen.py" --check-native-image "$NATIVE_DLL"

OSU_WORK="$STAGE/osu"
OSU_SEED="${RYTHME_OSU_SOURCE:-https://github.com/ppy/osu.git}"
FRAMEWORK_WORK="$STAGE/osu-framework"
FRAMEWORK_SEED="${RYTHME_FRAMEWORK_SOURCE:-https://github.com/ppy/osu-framework.git}"

echo "[*] cloning the full pinned osu!lazer source tree"
git clone --quiet --no-checkout "$OSU_SEED" "$OSU_WORK"
git -C "$OSU_WORK" checkout --quiet --detach "$PINNED_OSU_COMMIT"

ACTUAL_COMMIT="$(git -C "$OSU_WORK" rev-parse HEAD)"
if [ "$ACTUAL_COMMIT" != "$PINNED_OSU_COMMIT" ]; then
  echo "error: osu! checkout is $ACTUAL_COMMIT, expected $PINNED_OSU_COMMIT" >&2
  exit 1
fi

# A local partial-clone cache may know the commit while no longer being able to
# materialise every referenced project. Do not silently publish a reduced app.
for required_path in \
  osu.Game/osu.Game.csproj \
  osu.Game.Tournament/osu.Game.Tournament.csproj \
  osu.Desktop/osu.Desktop.csproj
do
  if [ ! -f "$OSU_WORK/$required_path" ]; then
    echo "error: osu! source checkout is incomplete (missing $required_path)" >&2
    exit 1
  fi
done

echo "[*] cloning the exact osu!framework source used by the pinned lazer tree"
git clone --quiet --no-checkout "$FRAMEWORK_SEED" "$FRAMEWORK_WORK"
git -C "$FRAMEWORK_WORK" checkout --quiet --detach "$PINNED_FRAMEWORK_COMMIT"

ACTUAL_FRAMEWORK_COMMIT="$(git -C "$FRAMEWORK_WORK" rev-parse HEAD)"
if [ "$ACTUAL_FRAMEWORK_COMMIT" != "$PINNED_FRAMEWORK_COMMIT" ]; then
  echo "error: osu!framework checkout is $ACTUAL_FRAMEWORK_COMMIT, expected $PINNED_FRAMEWORK_COMMIT" >&2
  exit 1
fi

# The pinned tree requests .NET 8.0.100. A newer SDK can compile its net8.0
# projects, but global.json would otherwise prevent SDK discovery.
if ! dotnet --list-sdks | grep -Eq '^8\.'; then
  if [ -f "$OSU_WORK/global.json" ]; then
    mv "$OSU_WORK/global.json" "$OSU_WORK/global.json.disabled"
  fi
  if [ -f "$FRAMEWORK_WORK/global.json" ]; then
    mv "$FRAMEWORK_WORK/global.json" "$FRAMEWORK_WORK/global.json.disabled"
  fi
fi

echo "[*] applying and testing the orphaned mouse-gesture framework fix"
git -C "$FRAMEWORK_WORK" apply "$ROOT/src/client/lazer-fork/upstream/osu-framework-input.patch"
git -C "$FRAMEWORK_WORK" diff --check
DOTNET_ROLL_FORWARD=Major dotnet test \
  "$FRAMEWORK_WORK/osu.Framework.Tests/osu.Framework.Tests.csproj" \
  --configuration Release \
  --filter 'FullyQualifiedName~WithoutButtonDownQueue' \
  --nologo \
  --verbosity minimal

dotnet remove "$OSU_WORK/osu.Game/osu.Game.csproj" package ppy.osu.Framework >/dev/null
(
  cd "$OSU_WORK"
  dotnet add osu.Game/osu.Game.csproj reference ../osu-framework/osu.Framework/osu.Framework.csproj >/dev/null
)
if grep -Fq 'PackageReference Include="ppy.osu.Framework"' "$OSU_WORK/osu.Game/osu.Game.csproj" ||
   ! grep -Fq 'osu-framework' "$OSU_WORK/osu.Game/osu.Game.csproj"; then
  echo "error: the lazer fork is not bound to the patched local framework" >&2
  exit 1
fi

echo "[*] applying the Rythme fork overlay"
git -C "$OSU_WORK" apply "$ROOT/src/client/lazer-fork/upstream/Rythme.patch"
cp -a "$ROOT/src/client/lazer-fork/overlay/osu.Desktop/." "$OSU_WORK/osu.Desktop/"
mkdir -p "$OSU_WORK/osu.Desktop/Rythme/Shared"
for source in \
  ArenaApi.cs \
  ArenaGameplayCompletion.cs \
  ArenaProtocolException.cs \
  ArenaRunFinalizer.cs \
  ArenaReceiptSummary.cs \
  CueDeliveryPolicy.cs \
  InputSender.cs \
  ArenaFrame.cs \
  BackgroundFailure.cs \
  BeatmapArchiveCache.cs \
  BeatmapMirrorClient.cs \
  BeatmapArchiveChart.cs \
  ClientIntegrityGuard.cs \
  ExecutableIntegrityVerifier.cs \
  StartupFailureReporter.cs \
  Protocol.cs \
  ProtocolValidation.cs \
  ManifestBuilder.cs \
  SealedRunState.cs \
  SecureChannel.cs \
  SecureRequestRecovery.cs \
  PoolCatalog.cs \
  UpstreamModCatalog.cs \
  RythmeJsonContext.cs \
  RythmeCancellationLifetime.cs \
  NativePolicyBridge.cs \
  NativePolicyIds.cs
do
  cp "$ROOT/src/client/Rythme.Desktop/$source" "$OSU_WORK/osu.Desktop/Rythme/Shared/$source"
done
NATIVE_DIGEST_PLACEHOLDERS="$(grep -Fo 'RYTHME_NATIVE_DIGEST_PLACEHOLDER' \
  "$OSU_WORK/osu.Desktop/Rythme/Shared/NativePolicyBridge.cs" | wc -l)"
if [ "$NATIVE_DIGEST_PLACEHOLDERS" -ne 1 ]; then
  echo "error: native policy source must contain exactly one digest placeholder" >&2
  exit 1
fi
sed -i \
  "s/RYTHME_NATIVE_DIGEST_PLACEHOLDER/$NATIVE_DIGEST/g" \
  "$OSU_WORK/osu.Desktop/Rythme/Shared/NativePolicyBridge.cs"
if grep -Fq 'RYTHME_NATIVE_DIGEST_PLACEHOLDER' \
  "$OSU_WORK/osu.Desktop/Rythme/Shared/NativePolicyBridge.cs"; then
  echo "error: native policy digest placeholder was not replaced" >&2
  exit 1
fi
mkdir -p "$OSU_WORK/osu.Desktop/Rythme/Native"
cp "$NATIVE_DLL" "$OSU_WORK/osu.Desktop/Rythme/Native/rythme_native.dll"
python3 "$ROOT/src/shared/clientpool.py" \
  "$ROOT/src/shared/pool.json" \
  "$OSU_WORK/osu.Desktop/Rythme/pool.json"
CLIENT_POOL="$OSU_WORK/osu.Desktop/Rythme/pool.json"
if grep -aEq '"(notes|at_ms)"' "$CLIENT_POOL"; then
  echo "error: metadata-only client pool contains server chart objects" >&2
  exit 1
fi

echo "[*] building and exercising a clean Linux lobby with hostile mouse timing"
DOTNET_ROLL_FORWARD=Major dotnet build \
  "$OSU_WORK/osu.Desktop/osu.Desktop.csproj" \
  --configuration Debug \
  -p:RythmeStartupSmoke=true \
  --nologo
echo "[*] testing a judgement before the replay recorder finishes loading"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$ROOT/src/client/lazer-fork/tools/replaycheck/replaycheck.csproj" \
  --configuration Debug \
  -p:OsuSource="$OSU_WORK" \
  --nologo
if [ -n "${RYTHME_BEATMAP_CACHE:-}" ]; then
  echo "[*] validating every playable map can complete before its arena deadline"
  DOTNET_ROLL_FORWARD=Major dotnet run \
    --project "$ROOT/src/client/lazer-fork/tools/playablecheck/playablecheck.csproj" \
    --configuration Release \
    -p:OsuSource="$OSU_WORK" \
    -- \
    "$ROOT/src/shared/pool.json" \
    "$RYTHME_BEATMAP_CACHE"
else
  echo "[*] playable-map completion validation skipped (RYTHME_BEATMAP_CACHE is not set)"
fi
bash "$ROOT/src/client/lazer-fork/tools/startup-smoke.sh" \
  "$OSU_WORK/osu.Desktop/bin/Debug/net8.0/Rythme" \
  "$STAGE/startup-smoke"

HARDENER_PROJECT="$ROOT/src/client/lazer-fork/tools/managed-harden/managed-harden.csproj"
echo "[*] renaming selected policy symbols in the temporary source tree"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- harden-source "$OSU_WORK/osu.Desktop"

PUBLISH="$STAGE/publish"
echo "[*] publishing the self-contained Windows x86-64 lazer fork"
DOTNET_ROLL_FORWARD=Major dotnet publish \
  "$OSU_WORK/osu.Desktop/osu.Desktop.csproj" \
  --configuration Release \
  --runtime win-x64 \
  --self-contained true \
  --output "$PUBLISH" \
  -p:PublishSingleFile=true \
  -p:EnableCompressionInSingleFile=true \
  -p:SatelliteResourceLanguages=en \
  -p:PublishReadyToRun=false \
  -p:IncludeNativeLibrariesForSelfExtract=true \
  -p:DebugType=None \
  -p:DebugSymbols=false \
  --nologo

echo "[*] auditing the final compiled policy assembly"
MANAGED_ASSEMBLY="$OSU_WORK/osu.Desktop/bin/Release/net8.0/win-x64/Rythme.dll"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- verify-assembly "$MANAGED_ASSEMBLY"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- verify-resource "$MANAGED_ASSEMBLY" Rythme.pool.json "$CLIENT_POOL"

echo "[*] verifying the exact managed and native files embedded in the single-file bundle"
FRAMEWORK_ASSEMBLY="$OSU_WORK/osu.Desktop/bin/Release/net8.0/win-x64/osu.Framework.dll"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- verify-bundle-entry "$PUBLISH/Rythme.exe" Rythme.dll "$MANAGED_ASSEMBLY"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- verify-bundle-entry \
  "$PUBLISH/Rythme.exe" \
  rythme_native.dll \
  "$NATIVE_DLL"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- verify-bundle-entry \
  "$PUBLISH/Rythme.exe" \
  osu.Framework.dll \
  "$FRAMEWORK_ASSEMBLY"

echo "[*] signing the complete executable image"
DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- sign \
  "$PUBLISH/Rythme.exe" \
  "$ROOT/htb/client-integrity-private.pem" \
  "$MANAGED_ASSEMBLY"

DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- verify "$PUBLISH/Rythme.exe" "$ROOT/htb/client-integrity-public.pem"

DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- verify-bound-assembly \
  "$PUBLISH/Rythme.exe" \
  "$ROOT/htb/client-integrity-public.pem" \
  "$MANAGED_ASSEMBLY"

DOTNET_ROLL_FORWARD=Major dotnet run \
  --project "$HARDENER_PROJECT" \
  --configuration Release \
  -- tamper-test "$PUBLISH/Rythme.exe" "$ROOT/htb/client-integrity-public.pem"

if ! file "$PUBLISH/Rythme.exe" | grep -q 'PE32+ executable (GUI) x86-64'; then
  echo "error: publish did not produce the expected Windows x86-64 GUI executable" >&2
  exit 1
fi
if grep -aFq "$ROOT" "$PUBLISH/Rythme.exe"; then
  echo "error: issued client leaks the author workspace path" >&2
  exit 1
fi
for symbol in \
  ArenaApi \
  ReadyAsync \
  SubmitCompletionAsync \
  SendSecureBytesAsync \
  ManifestResponse \
  ManifestSent \
  AttestedMods \
  AttestedMultiplier \
  BuildEnvelope \
  AppliedModSet \
  ReadyBinding \
  ClientIntegrityGuard \
  ExecutableIntegrityVerifier \
  NativePolicyBridge \
  NativePolicyIds \
  BuildModifierEnvelope \
  CreateCompletionFrame \
  SendArenaFrameAsync \
  ArenaFrame \
  ReadFinish \
  NativeChannelRequest
do
  if grep -aFq "$symbol" "$MANAGED_ASSEMBLY"; then
    echo "error: issued client still exposes protected symbol $symbol" >&2
    exit 1
  fi
done

for stale in \
  '/api/v2/crown/flag' \
  'FlagAsync' \
  'FlagResponse' \
  '/api/v2/heats/current/enroll' \
  '/api/v2/heats/current/ready' \
  '/api/v2/heats/current/cues' \
  '/api/v2/heats/current/input' \
  '/api/v2/heats/current/result' \
  '/api/v2/heats/current/manifest' \
  'rythme-run-manifest-v5' \
  'FinalizeAttestation' \
  'ready_capsule' \
  'judge_capsule' \
  'native_proof' \
  'server capsule rejected' \
  'native policy rejected the envelope' \
  'envelope'
do
  if grep -aFq "$stale" "$MANAGED_ASSEMBLY"; then
    echo "error: issued client exposes stale or descriptive protocol text $stale" >&2
    exit 1
  fi
done

DEST="$STAGE/$NAME"
mkdir -p "$DEST" "$ROOT/release"
RELEASE_STAGE="$(mktemp -d "$ROOT/release/.publish.XXXXXX")"
cp "$PUBLISH/Rythme.exe" "$DEST/Rythme.exe"
cp "$ROOT/challenge/CLIENT-README.txt" "$DEST/README.txt"
cp "$ROOT/challenge/THIRD-PARTY-NOTICES.txt" "$DEST/THIRD-PARTY-NOTICES.txt"
cp "$OSU_WORK/LICENCE" "$DEST/OSU-LICENCE.txt"

(cd "$STAGE" && zip -q -9 -r "$RELEASE_STAGE/$NAME.zip" "$NAME")

ZIP_LIST="$(unzip -Z1 "$RELEASE_STAGE/$NAME.zip")"
EXPECTED_LIST="$(printf '%s\n' \
  "$NAME/" \
  "$NAME/OSU-LICENCE.txt" \
  "$NAME/README.txt" \
  "$NAME/Rythme.exe" \
  "$NAME/THIRD-PARTY-NOTICES.txt" | sort)"
if [ "$(printf '%s\n' "$ZIP_LIST" | sort)" != "$EXPECTED_LIST" ]; then
  echo "error: player archive contains an unexpected file" >&2
  printf '%s\n' "$ZIP_LIST" >&2
  exit 1
fi

(
  cd "$RELEASE_STAGE"
  sha256sum "$NAME.zip" > SHA256SUMS
)

mv -f "$RELEASE_STAGE/$NAME.zip" "$OUT"
mv -f "$RELEASE_STAGE/SHA256SUMS" "$ROOT/release/SHA256SUMS"
rm -f "$LEGACY_DIRECT_EXE"

echo "[+] wrote $OUT ($(du -h "$OUT" | cut -f1))"
