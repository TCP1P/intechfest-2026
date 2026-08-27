import React from 'react';
import {fade} from '@remotion/transitions/fade';
import {linearTiming, TransitionSeries} from '@remotion/transitions';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {TrailerAudio} from './TrailerAudio';
import {LobbyScene} from './scenes/LobbyScene';
import {MenuScene} from './scenes/MenuScene';
import {PlayScene} from './scenes/PlayScene';
import {RevealScene} from './scenes/RevealScene';

export const FPS = 30;
export const RYTHME_TRAILER_FRAMES = 803;

const FadeToBlack: React.FC = () => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#000',
        opacity: interpolate(frame, [0, 4, 791, 802], [1, 0, 0, 1], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        }),
        pointerEvents: 'none',
      }}
    />
  );
};

export const RythmeTrailer: React.FC = () => {
  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <TransitionSeries name="Rythme teaser edit">
        <TransitionSeries.Sequence
          durationInFrames={306}
          name="Launch and enter Crown Arena"
        >
          <MenuScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({durationInFrames: 4})}
        />

        <TransitionSeries.Sequence
          durationInFrames={72}
          name="Lobby waiting state"
        >
          <LobbyScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({durationInFrames: 4})}
        />

        <TransitionSeries.Sequence
          durationInFrames={84}
          name="Beatmap and ruleset reveal"
        >
          <RevealScene />
        </TransitionSeries.Sequence>

        <TransitionSeries.Transition
          presentation={fade()}
          timing={linearTiming({durationInFrames: 5})}
        />

        <TransitionSeries.Sequence
          durationInFrames={354}
          name="Countdown and live play"
        >
          <PlayScene />
        </TransitionSeries.Sequence>
      </TransitionSeries>

      <TrailerAudio />
      <FadeToBlack />
    </AbsoluteFill>
  );
};
