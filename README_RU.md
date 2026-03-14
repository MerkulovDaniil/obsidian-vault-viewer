# Obsidian Vault Viewer

Self-hosted веб-интерфейс для просмотра Obsidian-хранилищ. Mobile-first дизайн, вдохновленный [notion4ever](https://github.com/MerkulovDaniil/notion4ever).

![Скриншот](screenshot.png)
<!-- Замените screenshot.png на реальный скриншот вашего хранилища -->

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

## Быстрый старт

```bash
pip install -r requirements.txt
python app.py --vault /путь/к/хранилищу
```

Откройте [http://localhost:8000](http://localhost:8000) в браузере.

### Одной командой

```bash
pip install fastapi uvicorn python-frontmatter markdown pyyaml && python app.py --vault /путь/к/хранилищу
```

## Конфигурация

| CLI аргумент | Переменная окружения | По умолчанию | Описание                           |
|-------------|----------------------|--------------|-------------------------------------|
| `--vault`   | `VAULT_ROOT`         | `./vault`    | Путь к Obsidian-хранилищу           |
| `--host`    | `VAULT_HOST`         | `0.0.0.0`    | Адрес привязки                      |
| `--port`    | `VAULT_PORT`         | `8000`       | Порт                                |
| `--title`   | `VAULT_NAME`         | имя папки    | Заголовок приложения в сайдбаре     |

CLI-аргументы имеют приоритет над переменными окружения.

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
ExecStart=/usr/bin/python3 /opt/vault-viewer/app.py --vault /путь/к/хранилищу --port 8000
WorkingDirectory=/opt/vault-viewer
Restart=always

[Install]
WantedBy=multi-user.target
```

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 8000
CMD ["python", "app.py", "--vault", "/vault"]
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
