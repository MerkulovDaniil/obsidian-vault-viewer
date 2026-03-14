# Obsidian Vault Viewer

Self-hosted веб-интерфейс для просмотра и редактирования Obsidian-хранилищ с любого устройства. Один Python-файл, zero config. Вдохновлен [notion4ever](https://github.com/MerkulovDaniil/notion4ever).

![Скриншот](screenshot.png)
<!-- Замените screenshot.png на реальный скриншот вашего хранилища -->

## Зачем?

Если ваш vault лежит на VPS или десктопе и вы хотите открывать его с телефона или любого браузера — это для вас. Все мои попытки синхронизировать vault между устройствами (Remotely Save и другие плагины) постоянно ломались: конфликты, тихая потеря данных, "failed to sync". Obsidian Sync работает, но стоит денег и требует приложение на каждом устройстве.

Этот viewer работает иначе: **vault остается в одном месте, а вы заходите с любого устройства**. Указываете папку, открываете URL — просматриваете, ищете, редактируете. It just works.

Рендерит большую часть того, что рендерит Obsidian: wiki-ссылки, embed-картинки, callout-блоки, KaTeX-формулы, frontmatter properties, обложки, Bases, иконки Iconic. Более того, иногда рендерит vault лучше самого Obsidian — я видел как он нормально отображает очень длинные файлы и глубоко вложенные папки, которые в приложении Obsidian ломаются. Не заменит Obsidian для сложных workflow с кучей плагинов или Dataview-запросами, но для чтения, быстрых правок и доступа к заметкам с телефона — это спасение.

## Возможности

- **Рендеринг Markdown** с полной поддержкой Obsidian: `[[вики-ссылки]]`, `![[встраивание]]`, callout-блоки, выделение, чекбоксы
- **KaTeX** для математических формул (`$inline$` и `$$display$$`)
- **Obsidian Bases** (файлы `.base`) с представлениями: карточки, список, таблица
- **Плагин Iconic** — иконки Lucide и эмодзи для файлов и папок
- **Обложки** из frontmatter (`banner`, `cover`, `image`)
- **Бейджи frontmatter** (статус, теги) с цветовой кодировкой
- **Полнотекстовый поиск** с мгновенной фильтрацией в сайдбаре и сниппетами
- **Карточки / Список / Таблица** для любой директории
- **Mobile-first** адаптивный дизайн
- **Редактирование и удаление** заметок в браузере
- **Блоки кода** с кнопкой копирования
- **Дерево файлов** в сайдбаре со сворачиваемыми папками

## Установка

```bash
pip install obsidian-vault-viewer
```

Затем запустите:

```bash
vault-viewer --vault /путь/к/хранилищу
```

Откройте [http://localhost:8000](http://localhost:8000) в браузере.

### Из исходников

```bash
git clone https://github.com/MerkulovDaniil/vault-viewer.git
cd vault-viewer
pip install .
vault-viewer --vault /путь/к/хранилищу
```

Или запуск без установки:

```bash
pip install fastapi uvicorn python-frontmatter markdown pyyaml
python app.py --vault /путь/к/хранилищу
```

## Конфигурация

### CLI-аргументы

| CLI аргумент | Переменная окружения | По умолчанию | Описание                           |
|-------------|----------------------|--------------|-------------------------------------|
| `--vault`   | `VAULT_ROOT`         | `./vault`    | Путь к Obsidian-хранилищу           |
| `--host`    | `VAULT_HOST`         | `0.0.0.0`    | Адрес привязки                      |
| `--port`    | `VAULT_PORT`         | `8000`       | Порт                                |
| `--title`   | `VAULT_NAME`         | имя папки    | Заголовок приложения в сайдбаре     |

### Файл конфигурации

Можно создать файл `vault-viewer.yml` (или `vault-viewer.yaml`) в рабочей директории:

```yaml
# Основное
vault: /путь/к/хранилищу
host: 0.0.0.0
port: 8000
title: Мои заметки

# Внешний вид
favicon: https://example.com/icon.png  # или локальный путь
custom_css: "body { font-size: 18px; }"
custom_head: '<script src="..."></script>'

# Поведение
pinch_zoom: true     # разрешить масштабирование на мобиле (по умолчанию: true)
readonly: false      # запретить редактирование/удаление (по умолчанию: false)
hide:                # паттерны для скрытия из сайдбара
  - "_private/**"
  - "*.tmp"
  - "drafts/**"
```

**Приоритет**: CLI-аргументы > файл конфигурации > переменные окружения > значения по умолчанию.

## Аутентификация

В приложении **нет встроенной аутентификации**. Рекомендуемые варианты:

1. **Cloudflare Access / Tunnel** (рекомендуется для публичного хостинга) -- zero-trust доступ перед приложением
2. **Reverse proxy с basic auth** (nginx, caddy)
3. **Запуск локально** -- привязка к `127.0.0.1` через `--host 127.0.0.1`

## Деплой

### systemd

```ini
[Unit]
Description=Obsidian Vault Viewer
After=network.target

[Service]
ExecStart=vault-viewer --vault /путь/к/хранилищу --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml app.py ./
COPY vault_viewer/ vault_viewer/
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["vault-viewer", "--vault", "/vault"]
```

```bash
docker run -v /путь/к/хранилищу:/vault -p 8000:8000 vault-viewer
```

## English

See [README.md](README.md) for documentation in English.

## Лицензия

[MIT](LICENSE)

## Благодарности

Вдохновлено [notion4ever](https://github.com/MerkulovDaniil/notion4ever). Построено на [FastAPI](https://fastapi.tiangolo.com/), [python-frontmatter](https://github.com/eyeseast/python-frontmatter) и [KaTeX](https://katex.org/).

Автор: [Daniil Merkulov](https://github.com/MerkulovDaniil)
