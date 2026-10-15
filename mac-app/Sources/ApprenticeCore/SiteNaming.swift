import Foundation

public enum SiteNaming {
    public static let browserBundleIdentifiers: Set<String> = [
        "com.apple.Safari", "com.google.Chrome", "company.thebrowser.Browser", "com.microsoft.edgemac",
        "com.brave.Browser", "org.mozilla.firefox",
    ]

    // ponytail: hand-picked multi-label public suffixes, not the full Public Suffix List.
    // Ship the PSL if sites from other ccTLDs come out wrong.
    static let multiLabelPublicSuffixes: Set<String> = [
        "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp", "ne.jp", "com.br",
        "com.mx", "co.in", "co.za", "com.cn", "com.hk", "com.sg", "com.tr", "co.kr", "com.ar", "co.il",
    ]

    /// Registrable domain label for browser pages (`www.booking.com` → `booking`), else the bundle id.
    public static func site(url: String?, appBundleIdentifier: String) -> String {
        guard let url, let host = URL(string: url)?.host?.lowercased(), !host.isEmpty else { return appBundleIdentifier }
        let hostLabels = host.split(separator: ".").map(String.init)
        let looksLikeAddress = hostLabels.allSatisfy { label in label.allSatisfy(\.isNumber) } || host.contains(":")
        if hostLabels.count < 2 || looksLikeAddress { return host }
        let lastTwoLabels = hostLabels.suffix(2).joined(separator: ".")
        let suffixLabelCount = hostLabels.count >= 3 && multiLabelPublicSuffixes.contains(lastTwoLabels) ? 2 : 1
        return hostLabels[hostLabels.count - suffixLabelCount - 1]
    }
}
