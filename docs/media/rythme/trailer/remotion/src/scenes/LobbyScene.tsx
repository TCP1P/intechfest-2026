import React from 'react';
import {Video} from '@remotion/media';
import {AbsoluteFill, staticFile} from 'remotion';

export const LobbyScene: React.FC = () => {
  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      <Video
        src={staticFile('lobby.mp4')}
        muted
        objectFit="cover"
        style={{height: '100%', width: '100%'}}
        name="Rythme lobby"
      />
    </AbsoluteFill>
  );
};
