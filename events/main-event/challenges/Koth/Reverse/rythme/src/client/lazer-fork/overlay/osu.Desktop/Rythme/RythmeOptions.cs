using System;
using System.Collections.Generic;

namespace osu.Desktop.Rythme
{
    internal sealed class RythmeOptions
    {
        private readonly object tokenGate = new object();
        private string authenticatedToken = string.Empty;

        public static readonly Uri ProductionServer = new Uri("https://rythme-private.tcp.1pc.tf/");

        public Uri Server { get; private init; } = ProductionServer;

        public string Token { get; private set; } = string.Empty;

        public bool AutoJoin { get; private init; }

        public bool HasAuthenticatedToken
        {
            get
            {
                lock (tokenGate)
                    return authenticatedToken.Length > 0;
            }
        }

        public static RythmeOptions Parse(IEnumerable<string> args)
        {
            string server = Environment.GetEnvironmentVariable("RYTHME_SERVER") ?? ProductionServer.AbsoluteUri;
            string token = Environment.GetEnvironmentVariable("RYTHME_TOKEN") ?? string.Empty;
            if (token.Length > 0)
                Environment.SetEnvironmentVariable("RYTHME_TOKEN", null);
            bool autoJoin = string.Equals(
                Environment.GetEnvironmentVariable("RYTHME_AUTO_JOIN"),
                "true",
                StringComparison.OrdinalIgnoreCase);

            foreach (string argument in args)
            {
                if (argument == "--autojoin")
                {
                    autoJoin = true;
                    continue;
                }

                string[] pair = argument.Split('=', 2);
                if (pair.Length != 2)
                    continue;

                switch (pair[0])
                {
                    case "--server":
                        server = pair[1];
                        break;

                    case "--token":
                        token = pair[1];
                        break;
                }
            }

            if (!server.EndsWith('/'))
                server += "/";

            if (!Uri.TryCreate(server, UriKind.Absolute, out Uri? serverUri) ||
                (serverUri.Scheme != Uri.UriSchemeHttp && serverUri.Scheme != Uri.UriSchemeHttps))
                throw new ArgumentException("The coordinator must be an absolute HTTP(S) URL.");
            if (serverUri.Scheme == Uri.UriSchemeHttp && !serverUri.IsLoopback)
                throw new ArgumentException("Remote coordinators require HTTPS.");

            return new RythmeOptions
            {
                Server = serverUri,
                Token = token,
                AutoJoin = autoJoin,
            };
        }

        public string TakeToken()
        {
            string token = Token;
            Token = string.Empty;
            return token;
        }

        public void RememberAuthenticatedToken(string token)
        {
            ArgumentException.ThrowIfNullOrWhiteSpace(token);
            lock (tokenGate)
                authenticatedToken = token;
        }

        public bool TryGetAuthenticatedToken(out string token)
        {
            lock (tokenGate)
            {
                token = authenticatedToken;
                return token.Length > 0;
            }
        }

        public void ForgetAuthenticatedToken(string token)
        {
            lock (tokenGate)
            {
                if (string.Equals(authenticatedToken, token, StringComparison.Ordinal))
                    authenticatedToken = string.Empty;
            }
        }

        public void ForgetAuthenticatedToken()
        {
            lock (tokenGate)
                authenticatedToken = string.Empty;
        }
    }
}
