# Domain context

## Glossary

- **Data Source（数据源）**：向 Beacon 提供任务状态的本机来源；可以是工具官方 Hook，也可以是用户配置的自定义数据源。
- **Custom Data Source（自定义数据源）**：由用户在本机创建的数据源。它通过适配器接入私有工具，但 Beacon 不理解、保存或分发该工具的私有协议。
- **Local Endpoint（本地端点）**：自定义数据源所订阅的本机入口，只允许 Unix Domain Socket 或回环地址上的 TCP 端口。
- **Adapter（适配器）**：用户本机的可执行程序或脚本，将本地端点中的私有消息转换成 Beacon 可接受的标准事件。
- **Normalized Event（标准事件）**：与具体工具无关的任务状态事件，包含任务标识、标题、状态、进度和可选发生时间。
