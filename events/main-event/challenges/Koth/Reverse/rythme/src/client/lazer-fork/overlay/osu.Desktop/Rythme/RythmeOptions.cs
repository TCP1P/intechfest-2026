using System;
using System.Collections.Generic;
using System.Net;

namespace osu.Desktop.Rythme
{
    internal sealed class RythmeOptions
    {
        private readonly object tokenGate = new object();
        private string authenticatedToken = string.Empty;

        public static readonly Uri ProductionServer = new Uri("http://127.0.0.1:1337/");

        public Uri Server { get; private set; } = ProductionServer;

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

            if (!tryParseServer(server, out Uri serverUri, out string error))
                throw new ArgumentException(error);

            return new RythmeOptions
            {
                Server = serverUri,
                Token = token,
                AutoJoin = autoJoin,
            };
        }

        public bool TrySetServer(string value, out string error)
        {
            if (!tryParseServer(value, out Uri server, out error))
                return false;
            Server = server;
            return true;
        }

        private static bool tryParseServer(string value, out Uri server, out string error)
        {
            value = value.Trim();
            if (!value.EndsWith('/'))
                value += "/";
            if (!Uri.TryCreate(value, UriKind.Absolute, out Uri? parsed) ||
                parsed.Scheme != Uri.UriSchemeHttp ||
                !string.IsNullOrEmpty(parsed.UserInfo) ||
                parsed.AbsolutePath != "/" ||
                !string.IsNullOrEmpty(parsed.Query) ||
                !string.IsNullOrEmpty(parsed.Fragment))
            {
                server = ProductionServer;
                error = "Enter one target origin, for example http://10.13.41.3:1337/.";
                return false;
            }
            if (!parsed.IsLoopback && !isPrivateIPv4(parsed.Host))
            {
                server = ProductionServer;
                error = "The target must be a private IPv4 address reached through the event VPN.";
                return false;
            }
            server = parsed;
            error = string.Empty;
            return true;
        }

        private static bool isPrivateIPv4(string host)
        {
            if (!IPAddress.TryParse(host, out IPAddress? address) ||
                address.AddressFamily != System.Net.Sockets.AddressFamily.InterNetwork)
                return false;
            byte[] bytes = address.GetAddressBytes();
            return bytes[0] == 10 ||
                   (bytes[0] == 172 && bytes[1] is >= 16 and <= 31) ||
                   (bytes[0] == 192 && bytes[1] == 168);
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
