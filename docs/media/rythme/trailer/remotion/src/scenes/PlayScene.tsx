import React from 'react';
import {Video} from '@remotion/media';
import {AbsoluteFill, staticFile} from 'remotion';

export const PlayScene: React.FC = () => {
  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <Video
        src={staticFile('play.mp4')}
        muted
        objectFit="cover"
        style={{height: '100%', width: '100%'}}
        name="Live osu!mania play"
      />
    </AbsoluteFill>
  );
};
