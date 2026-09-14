import SwiftUI
import CoreImage.CIFilterBuiltins

/// 设置 › 再加一台手机 / iPad:给当前空间铸一个 6 位加入码,显示数字 + 二维码。
/// 另一台设备用相机扫码(通用链接直接进 App),或在引导页「加入那个空间」里输数字。
/// 码 15 分钟有效,过期自动换新;对方兑换后这里立刻显示已加入。
struct AddDeviceView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var short: SBBackend.ShortCode?
    @State private var joined = false
    @State private var failed = false

    var body: some View {
        List {
            Section {
                VStack(spacing: 16) {
                    if let short {
                        if let img = qrImage(SBBackend.pairingBase + "/p/" + short.code) {
                            Image(uiImage: img)
                                .interpolation(.none)
                                .resizable()
                                .scaledToFit()
                                .frame(width: 196, height: 196)
                                .padding(12)
                                .background(Color.white, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                        }
                        Text(short.pretty)
                            .font(.system(size: 40, weight: .bold, design: .monospaced))
                            .foregroundStyle(Color.sbInk)
                            .textSelection(.enabled)
                        Text("Valid for 15 minutes; it renews by itself.")
                            .font(.caption)
                            .foregroundStyle(Color.sbInk3)
                    } else if failed {
                        Text("Couldn't get a code. Check the connection and try again.")
                            .font(.subheadline)
                            .foregroundStyle(Color.sbWaiting)
                        Button("Try again") { Task { await mint() } }
                            .buttonStyle(SBSecondaryButtonStyle())
                    } else {
                        ProgressView().padding(.vertical, 40)
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
            } header: {
                Text("On the other device")
            } footer: {
                Text("Scan this with the Camera app, or open SessionBell there, tap \"Join that space\" on the welcome screen and type the digits. Same Apple ID on both? iCloud Keychain usually does this by itself.")
            }
            Section {
                if joined {
                    Label("The other device joined 🎉", systemImage: "checkmark.circle.fill")
                        .font(.headline)
                        .foregroundStyle(Color.sbDone)
                } else {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Waiting for the other device…")
                            .font(.subheadline)
                            .foregroundStyle(Color.sbInk2)
                    }
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Color.sbBackground)
        .navigationTitle("Add a device")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
        }
        .tint(Color.sbAccentText)
        .task { await loop() }
    }

    private func mint() async {
        joined = false
        short = await SBBackend.mintShortCode()
        failed = short == nil
    }

    private func loop() async {
        await mint()
        while !Task.isCancelled {
            try? await Task.sleep(for: .seconds(5))
            if Task.isCancelled { return }
            guard let short, !short.isExpired else {
                if !failed || short == nil { await mint() }
                continue
            }
            if !joined,
               let obj = await SBBackend.getJSON("/api/pair/\(short.code)/status") as? [String: Any],
               obj["redeemed"] as? Bool == true {
                joined = true
            }
        }
    }

    private func qrImage(_ text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let out = filter.outputImage else { return nil }
        let scaled = out.transformed(by: CGAffineTransform(scaleX: 10, y: 10))
        guard let cg = CIContext().createCGImage(scaled, from: scaled.extent) else { return nil }
        return UIImage(cgImage: cg)
    }
}
