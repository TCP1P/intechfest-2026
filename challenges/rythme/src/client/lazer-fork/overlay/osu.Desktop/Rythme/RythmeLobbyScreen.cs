using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using osu.Framework.Allocation;
using osu.Framework.Audio;
using osu.Framework.Audio.Sample;
using osu.Framework.Bindables;
using osu.Framework.Extensions.Color4Extensions;
using osu.Framework.Graphics;
using osu.Framework.Graphics.Containers;
using osu.Framework.Graphics.Shapes;
using osu.Framework.Graphics.Sprites;
using osu.Framework.Input.Bindings;
using osu.Framework.Input.Events;
using osu.Framework.Screens;
using osu.Game.Beatmaps;
using osu.Game.Graphics;
using osu.Game.Graphics.Containers;
using osu.Game.Graphics.Sprites;
using osu.Game.Graphics.UserInterface;
using osu.Game.Graphics.UserInterfaceV2;
using osu.Game.Input.Bindings;
using osu.Game.Online;
using osu.Game.Overlays;
using osu.Game.Rulesets.Mods;
using osu.Game.Screens;
using osu.Game.Screens.Backgrounds;
using osu.Game.Screens.OnlinePlay.Match;
using osuTK;
using osuTK.Graphics;
using Rythme.Desktop;

namespace osu.Desktop.Rythme
{
    internal partial class RythmeLobbyScreen : OsuScreen, IKeyBindingHandler<GlobalAction>
    {
        private const float room_width = 1180;
        private const float room_height = 680;

        private readonly RythmeOptions options;
        private readonly string initialToken;
        private readonly RythmeLobbyAutomation automation;
        private readonly RythmeReconnectPolicy reconnectPolicy = new RythmeReconnectPolicy();
        private readonly Pool pool = PoolCatalog.Load();
        private readonly BeatmapArchiveCache beatmapCache = new BeatmapArchiveCache(BeatmapArchiveCache.DefaultDirectory);
        private readonly BeatmapMirrorClient beatmapDownloads = new BeatmapMirrorClient();
        private RythmeBeatmapLibrary beatmapLibrary = null!;

        [Resolved]
        private BeatmapManager beatmaps { get; set; } = null!;

        [Resolved(canBeNull: true)]
        private IOverlayManager? overlayManager { get; set; }

        [Resolved]
        private MusicController musicController { get; set; } = null!;

        private Container roomPanel = null!;
        private Container connectionPanel = null!;
        private OsuPasswordTextBox tokenBox = null!;
        private OsuTextFlowContainer connectionMessage = null!;
        private Box connectionMessageBackground = null!;
        private OsuTextFlowContainer status = null!;
        private OsuSpriteText roomTitle = null!;
        private OsuSpriteText phaseText = null!;
        private Box phaseBadgeBackground = null!;
        private RythmePhaseRail phaseRail = null!;
        private OsuSpriteText beatmapTitle = null!;
        private OsuSpriteText beatmapArtist = null!;
        private OsuSpriteText modeText = null!;
        private OsuSpriteText sourceText = null!;
        private OsuSpriteText commitmentText = null!;
        private OsuSpriteText participantCount = null!;
        private OsuSpriteText crownText = null!;
        private OsuSpriteText savedLoadoutText = null!;
        private FillFlowContainer performerList = null!;
        private RoundedButton connectButton = null!;
        private RoundedButton readyButton = null!;
        private DownloadButton beatmapDownloadButton = null!;
        private ProgressBar beatmapDownloadProgressBar = null!;
        private UserModSelectButton modsButton = null!;
        private RoundedButton disconnectButton = null!;
        private RythmeModSelectOverlay modSelectOverlay = null!;
        private IDisposable? modSelectOverlayRegistration;

        private Sample? samplePlayerJoined;
        private Sample? samplePlayerReady;
        private Sample? samplePlayerReadyAll;

        private RythmeCancellationLifetime? attempt;
        private Task? connectionTask;
        private Task? monitorTask;
        private Task? readyTask;
        private ArenaApi? api;
        private SessionResponse? session;
        private EnrollResponse? enrollment;
        private ArenaView? latestState;
        private RythmeRunContext? currentRun;
        private HeatView? preparedHeat;
        private Note[]? preparedNotes;
        private long preparedOffset;
        private string currentHeatId = "";
        private string preparedHeatId = "";
        private string failedHeatId = "";
        private string performerFingerprint = "";
        private int previousPerformerCount = -1;
        private int previousReadyCount;
        private readonly Bindable<DownloadState> beatmapDownloadState = new Bindable<DownloadState>(DownloadState.NotDownloaded);
        private double beatmapDownloadProgress;
        private int lastPoolProgressPercent = -1;
        private bool preparing;
        private bool autoReadying;
        private bool reconnectPending;
        private string presentedPhase = string.Empty;

        public override string Title => "Rythme Crown Lobby";

        public override bool AllowUserExit => true;

        public override bool HideOverlaysOnEnter => true;

        public RythmeLobbyScreen(RythmeOptions options)
        {
            this.options = options;
            initialToken = options.TakeToken();
            automation = new RythmeLobbyAutomation(options.AutoJoin);
        }

        protected override BackgroundScreen CreateBackground() => new BackgroundScreenDefault();

        [BackgroundDependencyLoader]
        private void load(OsuColour colours, AudioManager audio)
        {
            beatmapLibrary = new RythmeBeatmapLibrary(beatmaps);
            samplePlayerJoined = audio.Samples.Get(@"Multiplayer/player-joined");
            samplePlayerReady = audio.Samples.Get(@"Multiplayer/player-ready");
            samplePlayerReadyAll = audio.Samples.Get(@"Multiplayer/player-ready-all");

            LoadComponent(modSelectOverlay = new RythmeModSelectOverlay
            {
                Beatmap = { BindTarget = Beatmap },
                Ruleset = { BindTarget = Ruleset },
                SelectedMods = { BindTarget = Mods },
            });

            InternalChildren =
            [
                new Box
                {
                    RelativeSizeAxes = Axes.Both,
                    Colour = Color4.Black.Opacity(0.32f),
                },
                new DrawSizePreservingFillContainer
                {
                    TargetDrawSize = new Vector2(1280, 760),
                    Strategy = DrawSizePreservationStrategy.Minimum,
                    Children =
                    [
                        roomPanel = createRoomPanel(colours),
                        connectionPanel = createConnectionPanel(colours),
                    ],
                },
            ];
        }

        private Container createRoomPanel(OsuColour colours)
        {
            var root = new Container
            {
                Anchor = Anchor.Centre,
                Origin = Anchor.Centre,
                Width = room_width,
                Height = room_height,
            };

            root.Children =
            [
                panel(0, 0, 775, 82,
                    new Box
                    {
                        Width = 6,
                        RelativeSizeAxes = Axes.Y,
                        Colour = colours.Pink1,
                    },
                    roomTitle = new TruncatingSpriteText
                    {
                        X = 25,
                        Y = 15,
                        Width = 500,
                        Text = "CROWN ARENA • CONNECTING",
                        Font = OsuFont.GetFont(size: 26, weight: FontWeight.Bold),
                    },
                    new OsuSpriteText
                    {
                        X = 26,
                        Y = 49,
                        Text = "LEADERBOARD KOTH  •  ONE ACTIVE RUN PER TEAM",
                        Font = OsuFont.GetFont(size: 12, weight: FontWeight.SemiBold),
                        Colour = colours.Blue1,
                    },
                    new Container
                    {
                        Anchor = Anchor.CentreRight,
                        Origin = Anchor.CentreRight,
                        Margin = new MarginPadding { Right = 17 },
                        Width = 204,
                        Height = 48,
                        Masking = true,
                        CornerRadius = 9,
                        Children =
                        [
                            phaseBadgeBackground = new Box
                            {
                                RelativeSizeAxes = Axes.Both,
                                Colour = colours.Gray7.Opacity(0.94f),
                            },
                            phaseText = new OsuSpriteText
                            {
                                Anchor = Anchor.Centre,
                                Origin = Anchor.Centre,
                                Text = "OFFLINE",
                                Font = OsuFont.GetFont(size: 16, weight: FontWeight.Bold, fixedWidth: true),
                                Colour = colours.Yellow,
                            },
                        ],
                    }),
                panel(0, 94, 775, 318,
                    new SpriteIcon
                    {
                        X = 28,
                        Y = 31,
                        Size = new Vector2(31),
                        Icon = FontAwesome.Solid.Random,
                        Colour = colours.Pink1,
                    },
                    beatmapTitle = new TruncatingSpriteText
                    {
                        X = 78,
                        Y = 26,
                        Width = 660,
                        Text = "Random beatmap locked",
                        Font = OsuFont.GetFont(size: 31, weight: FontWeight.Bold),
                    },
                    beatmapArtist = new TruncatingSpriteText
                    {
                        X = 80,
                        Y = 68,
                        Width = 658,
                        Text = "The song is revealed to every player together",
                        Font = OsuFont.GetFont(size: 18),
                        Colour = Color4.LightGray,
                    },
                    new Box
                    {
                        X = 28,
                        Y = 119,
                        Width = 719,
                        Height = 2,
                        Colour = Color4.White.Opacity(0.12f),
                    },
                    new OsuSpriteText
                    {
                        X = 29,
                        Y = 134,
                        Text = "WAVE FLOW",
                        Font = OsuFont.GetFont(size: 12, weight: FontWeight.Bold),
                        Colour = colours.GrayB,
                    },
                    phaseRail = new RythmePhaseRail
                    {
                        X = 29,
                        Y = 155,
                    },
                    modeText = new TruncatingSpriteText
                    {
                        X = 29,
                        Y = 211,
                        Width = 718,
                        Text = "4-MODE ROTATION  •  EVERY WAVE SCORES  •  MODS AFTER REVEAL",
                        Font = OsuFont.GetFont(size: 18, weight: FontWeight.Bold),
                        Colour = colours.Blue1,
                    },
                    sourceText = new TruncatingSpriteText
                    {
                        X = 29,
                        Y = 246,
                        Width = 718,
                        Text = "POOL  •  APPROVED LOCAL CACHE",
                        Font = OsuFont.GetFont(size: 14, weight: FontWeight.SemiBold),
                        Colour = colours.GreenLight,
                    },
                    commitmentText = new OsuSpriteText
                    {
                        X = 29,
                        Y = 284,
                        Text = "SELECTION COMMITMENT  —",
                        Font = OsuFont.GetFont(size: 12, fixedWidth: true),
                        Colour = colours.GrayA,
                    }),
                panel(0, 424, 775, 154,
                    new OsuSpriteText
                    {
                        X = 24,
                        Y = 17,
                        Text = "NEXT ACTION",
                        Font = OsuFont.GetFont(size: 12, weight: FontWeight.Bold),
                        Colour = colours.Pink1,
                    },
                    status = new OsuTextFlowContainer(text =>
                    {
                        text.Font = OsuFont.GetFont(size: 15);
                        text.Colour = Color4.White;
                    })
                    {
                        X = 24,
                        Y = 45,
                        Width = 727,
                        Height = 94,
                        Text = "Enter the Crown Arena to predownload the event pool and prepare your four saved loadouts.",
                    }),
                new Container
                {
                    Y = 590,
                    Width = 775,
                    Height = 90,
                    Children =
                    [
                        beatmapDownloadButton = new DownloadButton
                        {
                            Width = 58,
                            Height = 58,
                            Action = retryBeatmapDownload,
                            TooltipText = "The event pool downloads from its pinned mirror once; the coordinator is the fallback.",
                            Enabled = { Value = false },
                            State = { BindTarget = beatmapDownloadState },
                        },
                        readyButton = new RoundedButton
                        {
                            X = 73,
                            Width = 327,
                            Height = 58,
                            Text = "ENTER ARENA TO CONTINUE",
                            Action = primaryAction,
                            BackgroundColour = colours.Green,
                            Enabled = { Value = false },
                            TooltipText = "Press Enter to use the primary lobby action.",
                        },
                        loadoutModeButton = new RoundedButton
                        {
                            X = 415,
                            Width = 125,
                            Height = 58,
                            Text = "MODE  •  OSU!",
                            Action = () => cycleLoadoutMode(1),
                            BackgroundColour = colours.BlueDark,
                            Enabled = { Value = false },
                            TooltipText = "Choose which ruleset loadout to edit. Left/right arrows also switch modes.",
                        },
                        modsButton = new UserModSelectButton
                        {
                            Anchor = Anchor.TopRight,
                            Origin = Anchor.TopRight,
                            Width = 220,
                            Height = 58,
                            Text = "EDIT MODS  •  NM",
                            Action = handleModsAction,
                            Enabled = { Value = false },
                            TooltipText = "Open the native osu! mod selector. F1 also opens it.",
                        },
                        beatmapDownloadProgressBar = new ProgressBar(false)
                        {
                            RelativeSizeAxes = Axes.None,
                            X = 73,
                            Y = 54,
                            Width = 327,
                            Height = 4,
                            FillColour = colours.Blue,
                            BackgroundColour = Color4.Black.Opacity(0.45f),
                            Alpha = 0,
                        },
                    ],
                },
                panel(790, 0, 390, 680,
                    new OsuSpriteText
                    {
                        X = 20,
                        Y = 20,
                        Text = "PLAYERS",
                        Font = OsuFont.GetFont(size: 22, weight: FontWeight.Bold),
                    },
                    participantCount = new OsuSpriteText
                    {
                        Anchor = Anchor.TopRight,
                        Origin = Anchor.TopRight,
                        Margin = new MarginPadding { Top = 25, Right = 22 },
                        Text = "0 / 0 READY",
                        Font = OsuFont.GetFont(size: 12, weight: FontWeight.Bold),
                        Colour = colours.GrayB,
                    },
                    new OsuScrollContainer
                    {
                        X = 16,
                        Y = 64,
                        Width = 358,
                        Height = 399,
                        Child = performerList = new FillFlowContainer
                        {
                            RelativeSizeAxes = Axes.X,
                            AutoSizeAxes = Axes.Y,
                            Direction = FillDirection.Vertical,
                            Spacing = new Vector2(0, 7),
                        },
                    },
                    new Box
                    {
                        X = 18,
                        Y = 475,
                        Width = 354,
                        Height = 1,
                        Colour = Color4.White.Opacity(0.12f),
                    },
                    new OsuSpriteText
                    {
                        X = 20,
                        Y = 492,
                        Text = "SAVED LOADOUTS",
                        Font = OsuFont.GetFont(size: 12, weight: FontWeight.Bold),
                        Colour = colours.Blue1,
                    },
                    savedLoadoutText = new OsuSpriteText
                    {
                        X = 20,
                        Y = 516,
                        Width = 350,
                        Text = "OSU! NM  •  TAIKO NM  •  CATCH NM  •  MANIA NM",
                        Font = OsuFont.GetFont(size: 13, weight: FontWeight.SemiBold),
                        Colour = Color4.LightGray,
                    },
                    new Box
                    {
                        X = 18,
                        Y = 559,
                        Width = 354,
                        Height = 1,
                        Colour = Color4.White.Opacity(0.12f),
                    },
                    new SpriteIcon
                    {
                        X = 21,
                        Y = 579,
                        Size = new Vector2(18),
                        Icon = FontAwesome.Solid.Crown,
                        Colour = colours.Yellow,
                    },
                    crownText = new TruncatingSpriteText
                    {
                        X = 50,
                        Y = 577,
                        Width = 310,
                        Text = "CROWN  •  UNCLAIMED",
                        Font = OsuFont.GetFont(size: 14, weight: FontWeight.Bold),
                        Colour = colours.Yellow,
                    },
                    disconnectButton = new RoundedButton
                    {
                        X = 18,
                        Y = 625,
                        Width = 354,
                        Height = 37,
                        Text = "USE ANOTHER TOKEN",
                        Action = changeToken,
                        BackgroundColour = colours.Gray7,
                    }),
            ];

            return root;
        }

        private Container createConnectionPanel(OsuColour colours)
        {
            return new Container
            {
                RelativeSizeAxes = Axes.Both,
                Depth = -10,
                Children =
                [
                    new Box
                    {
                        RelativeSizeAxes = Axes.Both,
                        Colour = Color4.Black.Opacity(0.64f),
                    },
                    new Container
                    {
                        Anchor = Anchor.Centre,
                        Origin = Anchor.Centre,
                        Width = 760,
                        Height = 400,
                        Masking = true,
                        CornerRadius = 16,
                        Children =
                        [
                            new Box
                            {
                                RelativeSizeAxes = Axes.Both,
                                Colour = new Color4(24, 27, 45, 252),
                            },
                            new Box
                            {
                                RelativeSizeAxes = Axes.X,
                                Height = 6,
                                Colour = colours.Pink2,
                            },
                            new FillFlowContainer
                            {
                                RelativeSizeAxes = Axes.X,
                                AutoSizeAxes = Axes.Y,
                                Padding = new MarginPadding(34),
                                Spacing = new Vector2(0, 8),
                                Direction = FillDirection.Vertical,
                                Children =
                                [
                                    new OsuSpriteText
                                    {
                                        Text = "ENTER CROWN ARENA",
                                        Font = OsuFont.GetFont(size: 36, weight: FontWeight.Bold),
                                        Colour = colours.Pink1,
                                    },
                                    new OsuSpriteText
                                    {
                                        Text = "ONE TOKEN  •  EVERY WAVE  •  YOUR SAVED LOADOUTS",
                                        Font = OsuFont.GetFont(size: 16, weight: FontWeight.SemiBold),
                                        Colour = colours.Blue1,
                                    },
                                    new OsuSpriteText
                                    {
                                        Text = "Join once, predownload the event pool, then leave AUTO START armed for hands-free waves.",
                                        Font = OsuFont.GetFont(size: 14),
                                        Colour = Color4.LightGray,
                                    },
                                    new OsuSpriteText
                                    {
                                        Text = $"SECURE SESSION  •  {options.Server.Host}",
                                        Font = OsuFont.GetFont(size: 14, weight: FontWeight.SemiBold),
                                        Colour = colours.GreenLight,
                                    },
                                    new OsuSpriteText
                                    {
                                        Text = "1  COPY YOUR ARENA TOKEN FROM RSCTF",
                                        Font = OsuFont.GetFont(size: 12, weight: FontWeight.Bold),
                                        Colour = colours.GrayB,
                                    },
                                    new OsuSpriteText
                                    {
                                        Text = "Open the Rythme challenge and copy “Your arena capability”.",
                                        Font = OsuFont.GetFont(size: 13),
                                        Colour = Color4.LightGray,
                                    },
                                    tokenBox = new OsuPasswordTextBox
                                    {
                                        PlaceholderText = "2  Paste the koth_… arena token",
                                        Text = initialToken,
                                        RelativeSizeAxes = Axes.X,
                                    },
                                    connectButton = new RoundedButton
                                    {
                                        RelativeSizeAxes = Axes.X,
                                        Height = 50,
                                        Text = "3  ENTER CROWN ARENA",
                                        Action = connect,
                                        BackgroundColour = colours.Pink2,
                                    },
                                    new Container
                                    {
                                        RelativeSizeAxes = Axes.X,
                                        Height = 66,
                                        Masking = true,
                                        CornerRadius = 8,
                                        Children =
                                        [
                                            connectionMessageBackground = new Box
                                            {
                                                RelativeSizeAxes = Axes.Both,
                                                Colour = Color4Extensions.FromHex("#283145").Opacity(0.9f),
                                            },
                                            connectionMessage = new OsuTextFlowContainer(text =>
                                            {
                                                text.Font = OsuFont.GetFont(size: 13);
                                                text.Colour = Color4.White;
                                            })
                                            {
                                                X = 14,
                                                Y = 11,
                                                Width = 664,
                                                Height = 44,
                                                Text = "Your token is used only to create the private arena session. It is never shown in the lobby.",
                                            },
                                        ],
                                    },
                                ],
                            },
                        ],
                    },
                ],
            };
        }

        private static Container panel(float x, float y, float width, float height, params Drawable[] children) => new Container
        {
            X = x,
            Y = y,
            Width = width,
            Height = height,
            Masking = true,
            CornerRadius = 10,
            Children =
            [
                new Box
                {
                    RelativeSizeAxes = Axes.Both,
                    Colour = new Color4(23, 26, 39, 238),
                },
                .. children,
            ],
        };

        protected override void LoadComplete()
        {
            base.LoadComplete();
            modSelectOverlayRegistration = overlayManager?.RegisterBlockingOverlay(modSelectOverlay);
            if (Mods.Value == null)
                Mods.Value = Array.Empty<Mod>();
            Mods.BindValueChanged(onModsChanged, true);
#if RYTHME_UI_CAPTURE
            if (applyLobbyCaptureFixture())
                return;
#endif
            if (options.AutoJoin || options.HasAuthenticatedToken)
                Schedule(connect);
        }

        private void connect()
        {
            reconnectPending = false;
            reconnectPolicy.Reset();
            connectCore();
        }

        private void connectCore()
        {
            if (connectionTask is { IsCompleted: false })
                return;

            Uri server = options.Server;

            string token = tokenBox.Text.Trim();
            if (string.IsNullOrWhiteSpace(token) && !options.TryGetAuthenticatedToken(out token))
            {
                setConnectionMessage(
                    "Paste the koth_… token shown under “Your arena capability” in RSCTF.",
                    ConnectionTone.Warning);
                return;
            }

            disconnectState();
            attempt = new RythmeCancellationLifetime();
            connectButton.Enabled.Value = false;
            connectionTask = connectAndMonitor(server, token, attempt.Token);
        }

        private async Task connectAndMonitor(Uri server, string token, CancellationToken cancellationToken)
        {
            var candidateApi = new ArenaApi(server);
            try
            {
                setConnectionMessage($"Creating a private session with {server.Host}…", ConnectionTone.Info);
                SessionResponse newSession;
                try
                {
                    newSession = await candidateApi.CreateRctfSessionAsync(token, cancellationToken).ConfigureAwait(false);
                    options.RememberAuthenticatedToken(token);
                    reconnectPolicy.Reset();
                }
                catch (ArenaProtocolException error) when (error.StatusCode == 401)
                {
                    options.ForgetAuthenticatedToken(token);
                    throw;
                }
                finally
                {
                    token = string.Empty;
                }

                api = candidateApi;
                session = newSession;
                Schedule(() =>
                {
                    tokenBox.Text = string.Empty;
                    connectionPanel.Hide();
                    disconnectButton.Enabled.Value = true;
                    status.Text = $"JOINED AS {newSession.TeamName.ToUpperInvariant()}\n" +
                                  "Predownloading from the pinned mirror and verifying the event pool before entering a heat.";
                });
                await warmBeatmapPool(candidateApi, cancellationToken).ConfigureAwait(false);
                Schedule(() =>
                {
                    status.Text = $"JOINED AS {newSession.TeamName.ToUpperInvariant()}\n" +
                                  $"EVENT POOL READY  •  {pool.Songs.Length}/{pool.Songs.Length} MAPS PREPARED\n" +
                                  "Choose a mode, open EDIT MODS, then arm AUTO START once for hands-free waves.";
                });
                await monitorRoom(cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                candidateApi.Dispose();
            }
            catch (Exception error)
            {
                if (ReferenceEquals(api, candidateApi))
                    api = null;
                candidateApi.Dispose();
                session = null;
                if (options.HasAuthenticatedToken)
                    beginAutomaticReconnect(error);
                else
                    showConnectionFailure(error);
            }
        }

        private async Task warmBeatmapPool(ArenaApi currentApi, CancellationToken cancellationToken)
        {
            BeatmapCacheEntry[] entries = pool.Songs
                .Select(song => new BeatmapCacheEntry(song.Id, song.ArchiveSha256, song.SourceUrl))
                .ToArray();
            lastPoolProgressPercent = -1;
            await beatmapCache.WarmAsync(
                    entries,
                    (entry, progress, token) => downloadBeatmap(currentApi, entry, progress, token),
                    setPoolDownloadProgress,
                    cancellationToken,
                    async (entry, archive, token) =>
                    {
                        Song song = pool.Songs.Single(candidate => candidate.Id == entry.SongId);
                        setPoolImportProgress(song);
                        _ = await beatmapLibrary.PrepareAsync(archive, song, token).ConfigureAwait(false);
                    })
                .ConfigureAwait(false);
        }

        private void setPoolImportProgress(Song song)
        {
            int position = Array.FindIndex(pool.Songs, candidate => candidate.Id == song.Id) + 1;
            Schedule(() =>
            {
                if (IsDisposed)
                    return;

                beatmapDownloadState.Value = DownloadState.Importing;
                beatmapDownloadButton.TooltipText =
                    $"Preparing {song.Title} in the local lazer library ({position}/{pool.Songs.Length}).";
                readyButton.Enabled.Value = false;
                readyButton.Text = $"PREPARING MAPS  •  {position}/{pool.Songs.Length}";
                status.Text = $"PREPARING EVENT POOL  •  {position}/{pool.Songs.Length}\n" +
                              $"{song.Title} [{song.Difficulty}]\n" +
                              "Importing verified maps before any synchronized heat can begin.";
            });
        }

        private Task<byte[]> downloadBeatmap(
            ArenaApi currentApi,
            BeatmapCacheEntry entry,
            Action<double>? progress,
            CancellationToken cancellationToken) => beatmapDownloads.DownloadAsync(
            entry,
            (fallbackProgress, token) => currentApi.DownloadBeatmapAsync(entry.SongId, fallbackProgress, token),
            progress,
            cancellationToken);

        private void setPoolDownloadProgress(BeatmapPoolProgress progress)
        {
            int percent = (int)Math.Floor(progress.Overall * 100);
            if (percent == lastPoolProgressPercent && progress.Completed < progress.Total)
                return;
            lastPoolProgressPercent = percent;

            Schedule(() =>
            {
                if (IsDisposed)
                    return;

                bool complete = progress.Completed >= progress.Total;
                beatmapDownloadProgress = progress.Overall;
                beatmapDownloadState.Value = complete ? DownloadState.LocallyAvailable : DownloadState.Downloading;
                beatmapDownloadProgressBar.CurrentTime = progress.Overall;
                if (complete)
                    beatmapDownloadProgressBar.FadeOut(180);
                else
                    beatmapDownloadProgressBar.FadeIn(100);
                beatmapDownloadButton.Enabled.Value = false;
                beatmapDownloadButton.TooltipText = complete
                    ? $"All {progress.Total} event maps are verified and prepared in the local lazer library."
                    : $"Predownloading from the pinned mirror… {progress.Overall:P0}";
                readyButton.Enabled.Value = false;
                readyButton.Text = complete
                    ? $"EVENT POOL READY  •  {progress.Total}/{progress.Total}"
                    : $"POOL  {progress.Completed}/{progress.Total}  •  {progress.Overall:P0}";
                status.Text = complete
                    ? $"EVENT POOL READY  •  {progress.Total}/{progress.Total} MAPS PREPARED\nRevealed maps will load without import work."
                    : $"PREDOWNLOADING EVENT POOL  •  {progress.Completed}/{progress.Total}\n" +
                      $"{progress.SongId}  •  {progress.CurrentDownload:P0}\n" +
                      "Pinned mirror first; coordinator fallback is automatic. Future launches reuse the cache.";
            });
        }

        private void startMonitor()
        {
            RythmeCancellationLifetime? currentAttempt = attempt;
            if (currentAttempt == null || currentAttempt.IsCancellationRequested || api == null || session == null)
                return;
            if (monitorTask is { IsCompleted: false })
                return;
            CancellationToken cancellationToken = currentAttempt.Token;
            monitorTask = Task.Run(() => monitorRoomSafely(cancellationToken), CancellationToken.None);
        }

        private async Task monitorRoomSafely(CancellationToken cancellationToken)
        {
            try
            {
                await monitorRoom(cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
            }
            catch (Exception error)
            {
                beginAutomaticReconnect(error);
            }
        }

        private void beginAutomaticReconnect(Exception error) => Schedule(() =>
        {
            if (IsDisposed || reconnectPending)
                return;

            disconnectState();
            if (!options.HasAuthenticatedToken)
            {
                showConnectionFailure(error);
                return;
            }

            reconnectPending = true;
            int delay = reconnectPolicy.NextDelayMilliseconds();
            applyConnectionMessage(
                $"The arena link changed and the secure session was closed. " +
                $"Restoring it automatically in {delay / 1000.0:0.0}s…\n{error.Message}",
                ConnectionTone.Warning);
            connectionPanel.Show();
            connectButton.Enabled.Value = true;
            Scheduler.AddDelayed(() =>
            {
                if (!reconnectPending || IsDisposed || !options.HasAuthenticatedToken)
                    return;

                reconnectPending = false;
                connectCore();
            }, delay);
        });

        private void showConnectionFailure(Exception error) => Schedule(() =>
        {
            if (IsDisposed)
                return;

            applyConnectionMessage(
                $"Could not enter the arena. {friendlyConnectionError(error)}",
                ConnectionTone.Error);
            connectionPanel.Show();
            connectButton.Enabled.Value = true;
        });

        private async Task monitorRoom(CancellationToken cancellationToken)
        {
            while (currentRun == null)
            {
                ArenaApi currentApi = api ?? throw new InvalidOperationException("The room connection was closed.");
                ArenaView state = await currentApi.StateAsync(cancellationToken).ConfigureAwait(false);
                latestState = state;

                if (!string.Equals(currentHeatId, state.Heat.Id, StringComparison.Ordinal))
                {
                    currentHeatId = state.Heat.Id;
                    automation.ObserveHeat(state.Heat.Id);
                    enrollment = null;
                    preparedHeat = null;
                    preparedNotes = null;
                    preparedHeatId = "";
                    preparedOffset = 0;
                    failedHeatId = "";
                    preparing = false;
                    performerFingerprint = "";
                    previousPerformerCount = -1;
                    previousReadyCount = 0;
                    Schedule(() =>
                    {
                        stopLobbyPreview();
                        modSelectOverlay.Hide();
                        setModsWithoutSaving(Array.Empty<Mod>());
                        beatmapDownloadProgress = 0;
                        beatmapDownloadState.Value = DownloadState.NotDownloaded;
                        beatmapDownloadProgressBar.CurrentTime = 0;
                        beatmapDownloadProgressBar.FadeOut(100);
                        beatmapDownloadButton.TooltipText = "The event pool is cached; the revealed map will load locally.";
                        beatmapDownloadButton.Enabled.Value = false;
                        updateFutureLoadoutDisplay(true);
                    });
                }

                renderState(state);

                if (automation.TryClaimEnrollment(
                        state.Heat.Id,
                        state.Heat.Phase,
                        enrollment?.HeatId == state.Heat.Id))
                {
                    try
                    {
                        await enrollForHeat(state, cancellationToken).ConfigureAwait(false);
                    }
                    catch (Exception error)
                    {
                        automation.SetArmed(false);
                        setStatus($"AUTO-ENROLL FAILED\n{error.Message}\nSelect the main button to retry this heat manually.");
                    }
                }

                EnrollResponse? currentEnrollment = enrollment;
                if (currentEnrollment != null &&
                    state.Heat.Id == currentEnrollment.HeatId &&
                    state.Heat.Phase == "reveal" &&
                    state.Heat.Selection != null &&
                    !preparing &&
                    preparedHeatId != state.Heat.Id &&
                    failedHeatId != state.Heat.Id)
                {
                    preparing = true;
                    renderState(state);
                    try
                    {
                        if (!await prepareModSelection(state, cancellationToken).ConfigureAwait(false))
                        {
                            preparing = false;
                            continue;
                        }
                    }
                    catch (Exception error)
                    {
                        preparing = false;
                        failedHeatId = state.Heat.Id;
                        setBeatmapDownloadState(state.Heat.Id, DownloadState.NotDownloaded, 0);
                        setStatus($"MAP LOAD FAILED\n{error.Message}\nSelect the download icon to repair this cached map.");
                        renderState(state);
                    }
                }

                if (automation.TryClaimReady(
                        state.Heat.Id,
                        state.Heat.Phase,
                        preparedHeatId == state.Heat.Id,
                        currentRun != null))
                {
                    autoReadying = true;
                    renderState(state);
                    try
                    {
                        await autoReadyAndLaunch(state, cancellationToken).ConfigureAwait(false);
                    }
                    catch (Exception error)
                    {
                        automation.SetArmed(false);
                        setStatus($"AUTO START FAILED\n{error.Message}\nAuto-start was disarmed; choose mods and ready manually.");
                    }
                    finally
                    {
                        autoReadying = false;
                        renderState(state);
                    }
                }

                await Task.Delay(150, cancellationToken).ConfigureAwait(false);
            }
        }

        private void primaryAction()
        {
            ArenaView? state = latestState;
            if (state == null || currentRun != null || preparing || autoReadying || readyTask is { IsCompleted: false })
                return;

            bool enrolled = enrollment?.HeatId == state.Heat.Id;
            bool prepared = preparedHeatId == state.Heat.Id;

            if (state.Heat.Phase == "reveal" && enrolled && prepared)
            {
                if (automation.Armed)
                {
                    automation.SetArmed(false);
                    status.Text = "AUTO START DISARMED FOR THIS AND FUTURE HEATS.\nSelect LOCK MODS & READY when you are ready to enter manually.";
                    renderState(state);
                    return;
                }

                readyForHeat();
                return;
            }

            if (state.Heat.Phase == "enrollment" && !enrolled)
            {
                if (automation.Armed)
                {
                    automation.SetArmed(false);
                    status.Text = "AUTO START DISARMED.\nAn enrollment already in flight may finish, but the client will not ready automatically.";
                }
                else
                {
                    automation.SetArmed(true);
                    _ = automation.TryClaimEnrollment(state.Heat.Id, state.Heat.Phase, false);
                    status.Text = $"AUTO START ARMED  •  FOUR SAVED LOADOUTS\n{loadoutSummary()}\n" +
                                  "Enrolling now; the matching mode loadout applies automatically after reveal.";
                    readyForHeat();
                }
                renderState(state);
                return;
            }

            bool armed = automation.ToggleArmed();
            status.Text = armed
                ? $"AUTO START ARMED  •  FOUR SAVED LOADOUTS\n{loadoutSummary()}\n" +
                  "Leave this lobby open; the matching mode loadout will launch automatically."
                : "AUTO START DISARMED.\nYou can still enroll and ready manually during the next wave.";
            renderState(state);
        }

        private void readyForHeat()
        {
            RythmeCancellationLifetime? currentAttempt = attempt;
            ArenaApi? currentApi = api;
            if (readyTask is { IsCompleted: false } || currentAttempt == null || currentApi == null || session == null)
                return;
            CancellationToken cancellationToken = currentAttempt.Token;

            AppliedModSet? selectedMods = preparedHeatId == latestState?.Heat.Id
                ? describeSelectedMods()
                : null;
            readyButton.Enabled.Value = false;
            if (selectedMods != null)
            {
                modSelectOverlay.Hide();
                modsButton.Enabled.Value = false;
                loadoutModeButton.Enabled.Value = false;
                status.Text = $"LOCKING {selectedMods.Acronym} WITH THE COORDINATOR.\n" +
                              "The mod controls will reopen automatically if ready-up is rejected.";
            }
            readyTask = Task.Run(async () =>
            {
                try
                {
                    ArenaView state = await currentApi.StateAsync(cancellationToken).ConfigureAwait(false);
                    EnrollResponse? currentEnrollment = enrollment;
                    if (currentEnrollment == null)
                        await enrollForHeat(state, cancellationToken).ConfigureAwait(false);
                    else
                        await readyAndLaunch(
                            state,
                            currentEnrollment,
                            selectedMods ?? throw new InvalidOperationException("Wait for the beatmap and ruleset reveal before selecting mods."),
                            cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    // A disconnect or screen shutdown owns this cancellation.
                }
                catch (Exception error)
                {
                    setStatus($"READY FAILED\n{error.Message}");
                }
                finally
                {
                    if (latestState != null)
                        renderState(latestState);
                }
            }, CancellationToken.None);
        }

        private async Task autoReadyAndLaunch(ArenaView state, CancellationToken cancellationToken)
        {
            string ruleset = state.Heat.Selection?.Ruleset
                             ?? throw new InvalidOperationException("The ruleset was not revealed before auto-start.");
            var selected = new TaskCompletionSource<AppliedModSet>(TaskCreationOptions.RunContinuationsAsynchronously);
            Schedule(() =>
            {
                try
                {
                    editingLoadoutRuleset = ruleset;
                    applySavedLoadout(ruleset);
                    AppliedModSet applied = describeSelectedMods();
                    status.Text = $"AUTO START  •  LOCKING {applied.Acronym}\n" +
                                  "The playfield is preconfigured and will open as soon as the coordinator confirms ready.";
                    updateModDisplay();
                    selected.TrySetResult(applied);
                }
                catch (Exception error)
                {
                    selected.TrySetException(error);
                }
            });

            AppliedModSet selectedMods = await selected.Task.WaitAsync(cancellationToken).ConfigureAwait(false);
            EnrollResponse currentEnrollment = enrollment
                                               ?? throw new InvalidOperationException("The auto-start enrollment was lost before readying.");
            await readyAndLaunch(state, currentEnrollment, selectedMods, cancellationToken).ConfigureAwait(false);
        }

        private async Task enrollForHeat(ArenaView state, CancellationToken cancellationToken)
        {
            if (state.Heat.Phase != "enrollment")
                throw new InvalidOperationException("This heat has already locked. Enroll in the next heat.");

            ArenaApi currentApi = api ?? throw new InvalidOperationException("The room connection was closed.");
            EnrollResponse newEnrollment = await currentApi.EnrollAsync(cancellationToken).ConfigureAwait(false);
            enrollment = newEnrollment;
            setStatus($"ENROLLED FOR {newEnrollment.HeatId.ToUpperInvariant()}\n" +
                      $"Selection commitment: {state.Heat.Commitment[..Math.Min(20, state.Heat.Commitment.Length)]}…\n" +
                      "Waiting for the shared song and mode reveal. Mods unlock next.");
        }

        private async Task<bool> prepareModSelection(ArenaView reveal, CancellationToken cancellationToken)
        {
            ArenaApi currentApi = api ?? throw new InvalidOperationException("The room connection was closed.");
            Selection selection = reveal.Heat.Selection ?? throw new InvalidOperationException("The selection was not revealed.");
            Song song = PoolCatalog.Resolve(pool, selection);
            setStatus($"ROTATION {selection.SeriesIndex:0000}  •  WAVE {selection.SeriesLeg}/4\n" +
                      $"REVEALED  {selection.SongTitle} [{selection.Difficulty}] / {displayRuleset(selection.Ruleset)} / {selection.StarRating:0.00}★\n" +
                      $"Controls: {selection.ControlProfile}\nLoading the verified package from the local event cache …");
            setBeatmapDownloadState(reveal.Heat.Id, DownloadState.Downloading, 0);
            string expectedArchive = string.IsNullOrWhiteSpace(selection.ArchiveSha256)
                ? song.ArchiveSha256
                : selection.ArchiveSha256;
            byte[] archive = await beatmapCache
                                   .GetAsync(
                                       new BeatmapCacheEntry(selection.SongId, expectedArchive, song.SourceUrl),
                                       (entry, progress, token) => downloadBeatmap(currentApi, entry, progress, token),
                                       progress => setBeatmapDownloadState(reveal.Heat.Id, DownloadState.Downloading, progress),
                                       cancellationToken)
                                   .ConfigureAwait(false);

            Note[] notes = BeatmapArchiveChart.Extract(archive, selection);
            string selectedBeatmapMd5 = BeatmapArchiveChart.SelectedBeatmapMd5(archive, selection);
            string digest = PoolCatalog.ChartDigest(song, selection.Ruleset, notes);
            if (!string.Equals(digest, selection.ChartDigest, StringComparison.Ordinal))
                throw new InvalidOperationException("The downloaded difficulty did not match the committed chart.");

            setBeatmapDownloadState(reveal.Heat.Id, DownloadState.Importing, 1);
            WorkingBeatmap working = await beatmapLibrary
                                           .PrepareAsync(archive, song, cancellationToken, selectedBeatmapMd5)
                                           .ConfigureAwait(false);
            long offset = await currentApi.MeasureServerOffsetAsync(cancellationToken).ConfigureAwait(false);
            ArenaView fresh = await currentApi.StateAsync(cancellationToken).ConfigureAwait(false);
            latestState = fresh;
            if (!RythmeHeatPreparation.CanCommit(
                    reveal.Heat.Id,
                    fresh.Heat.Id,
                    fresh.Heat.Phase,
                    fresh.Heat.StartAtUnixMs,
                    fresh.ServerNowUnixMs))
            {
                preparing = false;
                setStatus(string.Equals(reveal.Heat.Id, fresh.Heat.Id, StringComparison.Ordinal)
                    ? "REVEAL WINDOW CLOSED BEFORE MAP PREPARATION FINISHED.\nYour saved loadouts were preserved for the next heat."
                    : "A NEW HEAT OPENED WHILE THE PREVIOUS MAP WAS PREPARING.\nThe stale map was discarded; your saved loadouts were preserved.");
                renderState(fresh);
                return false;
            }

            var completion = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);

            Schedule(() =>
            {
                try
                {
                    long estimatedServerNow = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + offset;
                    if (!RythmeHeatPreparation.CanCommit(
                            reveal.Heat.Id,
                            fresh.Heat.Id,
                            fresh.Heat.Phase,
                            fresh.Heat.StartAtUnixMs,
                            estimatedServerNow))
                    {
                        preparing = false;
                        status.Text = "REVEAL WINDOW CLOSED BEFORE THE PLAYFIELD WAS COMMITTED.\n" +
                                      "The stale ready-up was cancelled; your saved loadouts were preserved.";
                        completion.SetResult(false);
                        return;
                    }

                    Beatmap.Value = working;
                    Ruleset.Value = working.BeatmapInfo.Ruleset;
                    editingLoadoutRuleset = selection.Ruleset;
                    applySavedLoadout(selection.Ruleset);
                    startLobbyPreview(working);
                    preparedHeat = fresh.Heat;
                    preparedNotes = notes;
                    preparedHeatId = reveal.Heat.Id;
                    preparedOffset = offset;
                    preparing = false;
                    beatmapDownloadProgress = 1;
                    beatmapDownloadState.Value = DownloadState.LocallyAvailable;
                    beatmapDownloadProgressBar.CurrentTime = 1;
                    beatmapDownloadProgressBar.FadeOut(180);
                    beatmapDownloadButton.TooltipText = "Predownloaded beatmap ready in the local lazer library.";
                    beatmapDownloadButton.Enabled.Value = false;
                    modsButton.Enabled.Value = true;
                    status.Text = $"ROTATION {selection.SeriesIndex:0000}  •  WAVE {selection.SeriesLeg}/4\n" +
                                  $"REVEALED  {selection.SongTitle} [{selection.Difficulty}] / {displayRuleset(selection.Ruleset)} / {selection.StarRating:0.00}★\n" +
                                  $"Controls: {selection.ControlProfile}\n" +
                                  (automation.Armed
                                      ? $"Lobby preview playing  •  AUTO START will lock {loadouts.Describe(selection.Ruleset)}."
                                      : "Lobby preview playing  •  Open EDIT MODS, then select LOCK MODS & READY.");
                    updateModDisplay();
                    completion.SetResult(true);
                }
                catch (Exception error)
                {
                    completion.SetException(error);
                }
            });
            return await completion.Task.WaitAsync(cancellationToken).ConfigureAwait(false);
        }

        private void setBeatmapDownloadState(string heatId, DownloadState state, double progress)
        {
            Schedule(() =>
            {
                if (latestState?.Heat.Id != heatId)
                    return;

                beatmapDownloadProgress = Math.Clamp(progress, 0, 1);
                beatmapDownloadState.Value = state;
                beatmapDownloadProgressBar.CurrentTime = beatmapDownloadProgress;

                if (state == DownloadState.Downloading)
                {
                    beatmapDownloadProgressBar.FadeIn(100);
                    beatmapDownloadButton.TooltipText = $"Downloading beatmap… {beatmapDownloadProgress:P0}";
                }
                else
                {
                    beatmapDownloadProgressBar.FadeOut(180);
                    beatmapDownloadButton.TooltipText = state == DownloadState.Importing
                        ? "Importing beatmap into the local lazer library."
                        : "Loading the revealed beatmap from the verified local cache.";
                }

                ArenaView? current = latestState;
                if (current != null && preparing)
                {
                    int ready = current.Heat.Performers.Count(player => player.Ready);
                    updateReadyButton(current, ready, current.Heat.Performers.Length);
                }
            });
        }

        private void retryBeatmapDownload()
        {
            ArenaView? state = latestState;
            if (state?.Heat.Selection == null ||
                enrollment?.HeatId != state.Heat.Id ||
                failedHeatId != state.Heat.Id ||
                preparing ||
                currentRun != null)
                return;

            failedHeatId = "";
            beatmapDownloadProgress = 0;
            beatmapDownloadState.Value = DownloadState.NotDownloaded;
            beatmapDownloadProgressBar.CurrentTime = 0;
            beatmapDownloadProgressBar.FadeOut(100);
            beatmapDownloadButton.Enabled.Value = false;
            status.Text = $"RETRYING  {state.Heat.Selection.SongTitle} / {displayRuleset(state.Heat.Selection.Ruleset)}\n" +
                          "Repairing the cached package, then importing it automatically.";
            renderState(state);
        }

        private async Task readyAndLaunch(
            ArenaView state,
            EnrollResponse currentEnrollment,
            AppliedModSet appliedMods,
            CancellationToken cancellationToken)
        {
            if (state.Heat.Phase != "reveal" ||
                preparedHeat == null ||
                preparedNotes == null ||
                preparedHeatId != state.Heat.Id ||
                currentEnrollment.HeatId != state.Heat.Id)
                throw new InvalidOperationException("The shared mod-selection window is not open.");

            Selection selection = state.Heat.Selection
                                  ?? throw new InvalidOperationException("The coordinator did not reveal a selection for this heat.");

            ArenaApi currentApi = api ?? throw new InvalidOperationException("The room connection was closed.");
            SessionResponse currentSession = session ?? throw new InvalidOperationException("The room session was closed.");
            ReadyBinding ready = await currentApi
                                           .ReadyAsync(
                                               currentEnrollment.HeatTicketId,
                                           selection.Ruleset,
                                           selection.Nonce,
                                           appliedMods,
                                           cancellationToken)
                                       .ConfigureAwait(false);
            AppliedModSet lockedMods = ready.AppliedMods;

            var context = new RythmeRunContext(
                currentApi,
                currentSession,
                currentEnrollment,
                preparedHeat,
                preparedNotes,
                preparedOffset,
                ready);
            currentRun = context;

            Schedule(() =>
            {
                stopLobbyPreview();
                modSelectOverlay.Hide();
                modsButton.Enabled.Value = false;
                long localStart = context.Heat.StartAtUnixMs - context.ServerOffsetMS;
                double seconds = Math.Max(0, localStart - DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) / 1000.0;
                status.Text = $"LOCKED  {context.Selection.SongTitle} / {displayRuleset(context.Selection.Ruleset)} / {lockedMods.Acronym}\n" +
                              $"Playback {lockedMods.RateMilli / 1000.0:0.00}x  •  score {lockedMods.ScoreMultiplierMilli / 1000.0:0.00}x\n" +
                              $"Entering the playfield. Match starts in {seconds:0.0}s.";
                readyButton.Text = "LOADING MATCH";
                readyButton.Enabled.Value = false;
                this.Push(new RythmePlayerLoader(() => new RythmePlayer(context)));
            });
        }

        private void startLobbyPreview(WorkingBeatmap working)
        {
            if (!this.IsCurrentScreen())
                return;

            working.PrepareTrackForPreview(true);
            musicController.EnsurePlayingSomething();
        }

        private void stopLobbyPreview()
        {
            WorkingBeatmap? working = Beatmap.Value;
            if (working?.TrackLoaded == true)
                working.Track.Looping = false;
            musicController.Stop();
        }

        private void renderState(ArenaView state) => Schedule(() =>
        {
            if (IsDisposed)
                return;

            int ready = state.Heat.Performers.Count(player => player.Ready);
            int total = state.Heat.Performers.Length;
            if (previousPerformerCount >= 0 && total > previousPerformerCount)
                samplePlayerJoined?.Play();
            if (ready > previousReadyCount)
            {
                if (total > 0 && ready == total)
                    samplePlayerReadyAll?.Play();
                else
                    samplePlayerReady?.Play();
            }
            previousPerformerCount = total;
            previousReadyCount = ready;

            roomTitle.Text = $"CROWN ARENA • ROTATION {state.Series.Index:0000} • WAVE {state.Series.CurrentLeg}/{state.Series.TotalLegs}";
            phaseText.Text = $"{RythmeLobbyPhase.DisplayName(state.Heat.Phase)}  •  {phaseRemaining(state)}";
            presentPhase(state.Heat.Phase);
            participantCount.Text = $"{ready} / {total} READY";
            crownText.Text = string.IsNullOrWhiteSpace(state.CrownOwnerName)
                ? "CROWN  •  UNCLAIMED"
                : $"CROWN  •  {state.CrownOwnerName.ToUpperInvariant()}";

            Selection? selection = state.Heat.Selection;
            if (selection == null)
            {
                beatmapTitle.Text = "Community beatmap locked";
                beatmapArtist.Text = "The hard difficulty and mode are revealed to every player together";
                modeText.Text = $"4-MODE ROTATION  •  WAVE {state.Series.CurrentLeg}/{state.Series.TotalLegs}  •  EVERY WAVE SCORES";
                sourceText.Text = "POOL  •  PINNED COMMUNITY DIFFICULTIES FROM THE EVENT TRACK LIST";
            }
            else
            {
                beatmapTitle.Text = selection.SongTitle;
                beatmapArtist.Text = selection.SongArtist;
                updateModDisplay();
                string origin = selection.Origin switch
                {
                    "osu-featured-artist" => "OSU! FEATURED ARTIST TRENDING CACHE",
                    "osu-community" or "osu-community-beatmap" => "OSU! COMMUNITY APPROVED CACHE",
                    "original" => "RYTHME ORIGINAL",
                    _ => "EVENT-APPROVED CACHE",
                };
                string mapper = string.IsNullOrWhiteSpace(selection.Mapper) ? "" : $"  •  MAPPED BY {selection.Mapper.ToUpperInvariant()}";
                sourceText.Text = $"{origin}  •  {selection.Difficulty.ToUpperInvariant()}  •  {selection.StarRating:0.00}★{mapper}";
            }
            commitmentText.Text = $"SELECTION COMMITMENT  {abbreviate(state.Heat.Commitment, 32)}";

            string fingerprint = string.Join("|", state.Heat.Performers.Select(player =>
                $"{player.TeamId}:{player.Ready}:{player.Frames}:{player.ManifestSent}:{player.SettledScore}:{player.Eligible}"));
            if (!string.Equals(performerFingerprint, fingerprint, StringComparison.Ordinal))
            {
                performerFingerprint = fingerprint;
                performerList.Clear();
                if (state.Heat.Performers.Length == 0)
                {
                    performerList.Add(new OsuSpriteText
                    {
                        Text = "No performers have joined this heat.",
                        Font = OsuFont.GetFont(size: 14),
                        Colour = Color4.LightGray,
                        Margin = new MarginPadding(10),
                    });
                }
                else
                {
                    foreach (PerformerView player in state.Heat.Performers)
                    {
                        performerList.Add(new RythmeParticipantRow(
                            player,
                            state.Heat.Phase,
                            player.TeamId == session?.TeamId,
                            player.TeamId == state.CrownOwnerId));
                    }
                }
            }

            updateReadyButton(state, ready, total);
        });

        private void updateReadyButton(ArenaView state, int ready, int total)
        {
            beatmapDownloadButton.Enabled.Value = false;

            if (currentRun != null)
            {
                readyButton.Text = "MATCH IN PROGRESS";
                readyButton.Enabled.Value = false;
                modsButton.Enabled.Value = false;
                loadoutModeButton.Enabled.Value = false;
                return;
            }
            if (autoReadying)
            {
                string selected = preparedHeat?.Selection == null
                    ? "SAVED MODS"
                    : loadouts.Describe(preparedHeat.Selection.Ruleset);
                readyButton.Text = $"AUTO START  •  LOCKING {compactMods(selected)}";
                readyButton.Enabled.Value = false;
                modsButton.Enabled.Value = false;
                loadoutModeButton.Enabled.Value = false;
                return;
            }
            if (preparing)
            {
                readyButton.Text = beatmapDownloadState.Value switch
                {
                    DownloadState.Downloading => $"MAP {beatmapDownloadProgress:P0}  •  {ready}/{total} READY",
                    DownloadState.Importing => $"IMPORTING MAP  •  {ready}/{total} READY",
                    _ => $"CHECKING MAP  •  {ready}/{total} READY",
                };
                readyButton.Enabled.Value = false;
                modsButton.Text = beatmapDownloadState.Value switch
                {
                    DownloadState.Downloading => $"MAP {beatmapDownloadProgress:P0}  •  MODS LOCKED",
                    DownloadState.Importing => "IMPORTING MAP  •  MODS LOCKED",
                    _ => "CHECKING MAP  •  MODS LOCKED",
                };
                modsButton.Enabled.Value = false;
                loadoutModeButton.Text = state.Heat.Selection == null
                    ? $"MODE  •  {RythmeModLoadouts.DisplayName(editingLoadoutRuleset)}"
                    : $"MODE  •  {RythmeModLoadouts.DisplayName(state.Heat.Selection.Ruleset)}";
                loadoutModeButton.Enabled.Value = false;
                return;
            }
            if (failedHeatId == state.Heat.Id)
            {
                readyButton.Text = "MAP FAILED  •  USE DOWNLOAD ICON";
                readyButton.Enabled.Value = false;
                beatmapDownloadButton.TooltipText = "Retry the arena beatmap download.";
                beatmapDownloadButton.Enabled.Value = state.Heat.Phase == "reveal";
                modsButton.Text = "RETRY MAP  •  MODS LOCKED";
                modsButton.Enabled.Value = false;
                loadoutModeButton.Enabled.Value = false;
                return;
            }
            if (enrollment?.HeatId == state.Heat.Id)
            {
                if (state.Heat.Phase == "enrollment")
                {
                    readyButton.Text = automation.Armed
                        ? $"AUTO START  •  REVEAL {phaseRemaining(state)}"
                        : $"ENROLLED  •  ARM AUTO  •  {phaseRemaining(state)}";
                    readyButton.Enabled.Value = readyTask is not { IsCompleted: false };
                    updateFutureLoadoutDisplay(true);
                    return;
                }
                if (state.Heat.Phase == "reveal" && preparedHeatId == state.Heat.Id)
                {
                    readyButton.Text = automation.Armed
                        ? $"AUTO START  •  {phaseRemaining(state)} LEFT"
                        : $"LOCK MODS & READY  •  {phaseRemaining(state)} LEFT";
                    readyButton.Enabled.Value = readyTask is not { IsCompleted: false };
                    beatmapDownloadButton.TooltipText = "Predownloaded beatmap ready in the local lazer library.";
                    modsButton.Enabled.Value = true;
                    loadoutModeButton.Enabled.Value = false;
                    return;
                }

                if (state.Heat.Phase == "reveal")
                {
                    readyButton.Text = $"PREPARING PLAYFIELD  •  {phaseRemaining(state)}";
                    readyButton.Enabled.Value = false;
                    modsButton.Text = "MODS APPLY AFTER MAP PREP";
                    modsButton.Enabled.Value = false;
                    loadoutModeButton.Enabled.Value = false;
                    return;
                }

                readyButton.Text = automation.Armed
                    ? $"AUTO START  •  NEXT {phaseRemaining(state)}"
                    : $"ARM AUTO START  •  NEXT {phaseRemaining(state)}";
                readyButton.Enabled.Value = session != null;
                updateFutureLoadoutDisplay(true);
                return;
            }
            if (state.Heat.Phase == "enrollment")
            {
                readyButton.Text = automation.Armed
                    ? $"AUTO-ENROLLING  •  {phaseRemaining(state)}"
                    : $"START + AUTO READY  •  {phaseRemaining(state)}";
                readyButton.Enabled.Value = session != null && readyTask is not { IsCompleted: false };
                updateFutureLoadoutDisplay(true);
                return;
            }

            readyButton.Text = automation.Armed
                ? $"AUTO START  •  NEXT {phaseRemaining(state)}"
                : $"ARM AUTO START  •  NEXT {phaseRemaining(state)}";
            readyButton.Enabled.Value = session != null;
            updateFutureLoadoutDisplay(true);
        }

        private static string phaseRemaining(ArenaView state)
        {
            long target = state.Heat.Phase switch
            {
                "enrollment" => state.Heat.EnrollmentEndsUnixMs,
                "reveal" => state.Heat.StartAtUnixMs,
                "running" => state.Heat.EndAtUnixMs,
                "submission" => state.Heat.SubmissionEndsUnixMs,
                _ => state.Heat.NextHeatAtUnixMs,
            };
            long remaining = Math.Max(0, target - state.ServerNowUnixMs);
            return TimeSpan.FromMilliseconds(remaining).ToString(@"mm\:ss");
        }

        private void presentPhase(string phase)
        {
            if (phase == presentedPhase)
                return;

            presentedPhase = phase;
            phaseRail.SetPhase(phase);
            (Color4 background, Color4 foreground) = phase switch
            {
                "enrollment" => (Color4Extensions.FromHex("#2E405C"), Color4Extensions.FromHex("#78D7FF")),
                "reveal" => (Color4Extensions.FromHex("#593866"), Color4Extensions.FromHex("#FF9BD0")),
                "running" => (Color4Extensions.FromHex("#29483D"), Color4Extensions.FromHex("#B8F06A")),
                "submission" => (Color4Extensions.FromHex("#5B482A"), Color4Extensions.FromHex("#FFE275")),
                "results" => (Color4Extensions.FromHex("#4D385D"), Color4Extensions.FromHex("#D4A7FF")),
                _ => (Color4Extensions.FromHex("#333849"), Color4.LightGray),
            };
            phaseBadgeBackground.Colour = background;
            phaseText.Colour = foreground;
        }

        private static string displayRuleset(string ruleset) => ruleset switch
        {
            "osu" => "OSU!",
            "taiko" => "OSU!TAIKO",
            "fruits" => "OSU!CATCH",
            "mania" => "OSU!MANIA",
            _ => ruleset.ToUpperInvariant(),
        };

        private static string abbreviate(string value, int length) =>
            value.Length <= length ? value : value[..length] + "…";

        private AppliedModSet describeSelectedMods()
        {
            return UpstreamModCatalog.DescribeApplied(
                Ruleset.Value,
                Beatmap.Value.BeatmapInfo.Difficulty,
                Mods.Value);
        }

        public bool OnPressed(KeyBindingPressEvent<GlobalAction> e)
        {
            if (e.Repeat)
                return false;

            switch (e.Action)
            {
                case GlobalAction.ToggleModSelection when modsButton.Enabled.Value:
                    handleModsAction();
                    return true;

                case GlobalAction.SelectPrevious when loadoutModeButton.Enabled.Value:
                    cycleLoadoutMode(-1);
                    return true;

                case GlobalAction.SelectNext when loadoutModeButton.Enabled.Value:
                    cycleLoadoutMode(1);
                    return true;

                case GlobalAction.Select when readyButton.Enabled.Value:
                    readyButton.TriggerClick();
                    return true;

                default:
                    return false;
            }
        }

        public void OnReleased(KeyBindingReleaseEvent<GlobalAction> e)
        {
        }

        private void disconnect()
        {
            reconnectPending = false;
            reconnectPolicy.Reset();
            disconnectState();
        }

        private void disconnectState()
        {
            attempt?.Cancel();
            attempt?.Dispose();
            attempt = null;
            api?.Dispose();
            api = null;
            session = null;
            enrollment = null;
            latestState = null;
            currentHeatId = "";
            preparedHeat = null;
            preparedNotes = null;
            preparedHeatId = "";
            preparedOffset = 0;
            failedHeatId = "";
            preparing = false;
            autoReadying = false;
            performerFingerprint = "";
            previousPerformerCount = -1;
            previousReadyCount = 0;
            lastPoolProgressPercent = -1;
            automation.ResetConnection();

            if (roomPanel != null)
                stopLobbyPreview();

            if (connectionPanel != null)
            {
                connectionPanel.Show();
                connectButton.Enabled.Value = true;
            }
            if (readyButton != null)
            {
                readyButton.Text = "ENTER ARENA TO CONTINUE";
                readyButton.Enabled.Value = false;
            }
            if (phaseRail != null)
            {
                presentedPhase = string.Empty;
                phaseRail.SetPhase("offline");
                phaseText.Text = "OFFLINE";
                presentPhase("offline");
            }
            if (beatmapDownloadButton != null)
            {
                beatmapDownloadProgress = 0;
                beatmapDownloadState.Value = DownloadState.NotDownloaded;
                beatmapDownloadProgressBar.CurrentTime = 0;
                beatmapDownloadProgressBar.FadeOut(100);
                beatmapDownloadButton.TooltipText = "The event pool downloads from its pinned mirror once; the coordinator is the fallback.";
                beatmapDownloadButton.Enabled.Value = false;
            }
            if (modsButton != null)
            {
                modSelectOverlay.Hide();
                setModsWithoutSaving(Array.Empty<Mod>());
                updateFutureLoadoutDisplay(false);
            }
        }

        private void changeToken()
        {
            options.ForgetAuthenticatedToken();
            disconnect();
            tokenBox.Text = string.Empty;
            setConnectionMessage(
                "Paste the replacement koth_… token shown under “Your arena capability” in RSCTF.",
                ConnectionTone.Info);
        }

        public override void OnResuming(ScreenTransitionEvent e)
        {
            base.OnResuming(e);
            RythmeRunContext? completedRun = currentRun;
            if (completedRun == null)
                return;

            string message;
            try
            {
                message = ArenaReceiptSummary.Build(
                    completedRun.Receipt,
                    completedRun.Failure,
                    completedRun.Warning,
                    completedRun.AppliedMods.Acronym,
                    completedRun.ReturnMessage);
            }
            catch (Exception error)
            {
                message = $"RUN LINK ERROR\n{error.Message}";
            }

            status.Text = message;
            completedRun.Dispose();
            if (ReferenceEquals(currentRun, completedRun))
                currentRun = null;
            enrollment = null;
            preparedHeat = null;
            preparedNotes = null;
            preparedHeatId = "";
            preparing = false;
            startLobbyPreview(Beatmap.Value);
            startMonitor();
        }

        private void setStatus(string message) => Schedule(() => status.Text = message);

        private void setConnectionMessage(string message, ConnectionTone tone) =>
            Schedule(() => applyConnectionMessage(message, tone));

        private void applyConnectionMessage(string message, ConnectionTone tone)
        {
            connectionMessage.Text = message;
            connectionMessageBackground.Colour = tone switch
            {
                ConnectionTone.Warning => Color4Extensions.FromHex("#55472A").Opacity(0.94f),
                ConnectionTone.Error => Color4Extensions.FromHex("#5A2D3B").Opacity(0.94f),
                _ => Color4Extensions.FromHex("#283145").Opacity(0.9f),
            };
        }

        private static string friendlyConnectionError(Exception error) => error switch
        {
            ArenaProtocolException { StatusCode: 401 } =>
                "The token was rejected or rotated. Copy the current token from the Rythme challenge and try again.",
            ArenaProtocolException { StatusCode: 0 } =>
                "The secure coordinator could not be reached. Check your internet connection, then retry.",
            _ => error.Message,
        };

        private enum ConnectionTone
        {
            Info,
            Warning,
            Error,
        }

        protected override void Dispose(bool isDisposing)
        {
            attempt?.Cancel();
            attempt?.Dispose();
            currentRun?.Dispose();
            api?.Dispose();
            beatmapDownloads.Dispose();
            modSelectOverlayRegistration?.Dispose();
            base.Dispose(isDisposing);
        }
    }
}
