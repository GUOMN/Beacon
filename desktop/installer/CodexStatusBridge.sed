[Version]
Class=IEXPRESS
SEDVersion=3

[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=准备安装 Codex 六灯蓝牙桥接。
DisplayLicense=
FinishMessage=安装完成，程序已经启动。
TargetName=C:\Users\guome\Documents\ESP32_code\ws2812\CodexStatusBridge_Setup.exe
FriendlyName=Codex 六灯蓝牙桥接安装程序
AppLaunched=install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=install.cmd
UserQuietInstCmd=install.cmd
SourceFiles=SourceFiles

[SourceFiles]
SourceFiles0=C:\Users\guome\Documents\ESP32_code\ws2812\desktop\installer\
SourceFiles1=C:\Users\guome\Documents\ESP32_code\ws2812\desktop\dist\

[SourceFiles0]
%FILE0%=

[SourceFiles1]
%FILE1%=

[Strings]
FILE0=install.cmd
FILE1=CodexStatusBridge.exe
