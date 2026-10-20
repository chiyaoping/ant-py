libtun2socks.so 放在此目录（由 CI 从 hev-socks5-tunnel releases 下载）。
Gradle 会自动把 src/main/jniLibs/<abi>/*.so 打进 APK 的 lib/<abi>/，Android 允许
从 nativeLibraryDir 执行，这是绕过 SELinux W^X（app 数据目录禁止 exec）的标准做法。
