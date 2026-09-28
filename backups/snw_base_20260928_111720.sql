-- MariaDB dump 10.19  Distrib 10.4.32-MariaDB, for Win64 (AMD64)
--
-- Host: 127.0.0.1    Database: snw_base
-- ------------------------------------------------------
-- Server version	10.4.32-MariaDB

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Table structure for table `areas`
--

DROP TABLE IF EXISTS `areas`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `areas` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre_visible` varchar(150) NOT NULL,
  `nombre_tabla_base` varchar(64) NOT NULL,
  `fecha_creacion` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_tabla_base` (`nombre_tabla_base`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `areas`
--

LOCK TABLES `areas` WRITE;
/*!40000 ALTER TABLE `areas` DISABLE KEYS */;
/*!40000 ALTER TABLE `areas` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `call_center_log`
--

DROP TABLE IF EXISTS `call_center_log`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `call_center_log` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `paciente_id` int(11) DEFAULT NULL,
  `nombre_paciente` varchar(150) DEFAULT NULL,
  `numero_paciente` varchar(20) DEFAULT NULL,
  `numero_call_center` varchar(20) NOT NULL,
  `plantilla_clave` varchar(50) DEFAULT NULL,
  `automatico` tinyint(1) NOT NULL DEFAULT 0,
  `estado` enum('enviado','error') NOT NULL DEFAULT 'enviado',
  `descripcion_error` varchar(255) DEFAULT NULL,
  `base_datos` varchar(50) DEFAULT NULL,
  `fecha_hora` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_numero` (`numero_call_center`),
  KEY `idx_fecha` (`fecha_hora`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `call_center_log`
--

LOCK TABLES `call_center_log` WRITE;
/*!40000 ALTER TABLE `call_center_log` DISABLE KEYS */;
/*!40000 ALTER TABLE `call_center_log` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `configuracion`
--

DROP TABLE IF EXISTS `configuracion`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `configuracion` (
  `clave` varchar(60) NOT NULL,
  `valor` text DEFAULT NULL,
  `actualizada` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`clave`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `configuracion`
--

LOCK TABLES `configuracion` WRITE;
/*!40000 ALTER TABLE `configuracion` DISABLE KEYS */;
INSERT INTO `configuracion` VALUES ('call_center_boton_mensaje','Hola, estoy interesado/a en la información que me enviaron.','2026-09-28 10:49:48'),('call_center_boton_mensaje_oferta','Hola, estoy interesado/a en la oferta que me enviaron.','2026-09-28 10:49:48'),('call_center_numeros','','2026-09-28 10:49:48'),('call_center_url','https://saludmentalparatodos.cl/telefonosmpt.php','2026-09-28 10:49:48'),('correo_destino','','2026-09-28 10:49:48'),('correo_emisor','','2026-09-28 10:49:48'),('entorno','desarrollo','2026-09-28 10:49:48'),('intervalo_ms','1000','2026-09-28 10:49:48'),('metodo_envio','simulado','2026-09-28 10:49:48'),('numeros_prueba_dev','','2026-09-28 10:49:48'),('numeros_prueba_prod','','2026-09-28 10:49:48'),('plantillas_badge_aprobada_minutos','2','2026-09-28 10:49:48'),('plantillas_revision_minutos','2','2026-09-28 10:49:48'),('sesion_expira_horas','5','2026-09-28 10:49:48'),('smtp_host','','2026-09-28 10:49:48'),('smtp_pass','','2026-09-28 10:49:48'),('smtp_port','587','2026-09-28 10:49:48'),('smtp_tls','true','2026-09-28 10:49:48'),('smtp_user','','2026-09-28 10:49:48'),('url_base','','2026-09-28 10:49:48'),('wa_business_account_id','','2026-09-28 10:49:48'),('wa_graph_version','v26.0','2026-09-28 10:49:48'),('wa_messaging_limit_24h','2000','2026-09-28 10:49:48'),('wa_moneda','USD','2026-09-28 10:49:48'),('wa_phone_id','','2026-09-28 10:49:48'),('wa_rate_limit_activo','true','2026-09-28 10:49:48'),('wa_rate_limit_espera_defecto_s','60','2026-09-28 10:49:48'),('wa_rate_limit_pausa_max_s','30','2026-09-28 10:49:48'),('wa_rate_limit_reintentos','3','2026-09-28 10:49:48'),('wa_rate_limit_umbral_pct','80','2026-09-28 10:49:48'),('wa_template_lang','es','2026-09-28 10:49:48'),('wa_template_nombre','','2026-09-28 10:49:48'),('wa_throughput_mps','10','2026-09-28 10:49:48'),('wa_token','','2026-09-28 10:49:48'),('wa_verify_token','','2026-09-28 10:49:48'),('wa_webhook_path','/api/whatsapp/webhook','2026-09-28 10:49:48');
/*!40000 ALTER TABLE `configuracion` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `envios`
--

DROP TABLE IF EXISTS `envios`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `envios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `base_datos` varchar(50) DEFAULT NULL,
  `plantilla_clave` varchar(50) DEFAULT NULL,
  `plantilla_nombre` varchar(100) DEFAULT NULL,
  `total_pacientes` int(11) DEFAULT 0,
  `enviados` int(11) DEFAULT 0,
  `fallidos` int(11) DEFAULT 0,
  `invalidos` int(11) DEFAULT 0,
  `estado` enum('completado','cancelado','rechazado') NOT NULL DEFAULT 'completado',
  `comentario` varchar(255) DEFAULT NULL,
  `area_id` int(11) DEFAULT NULL,
  `tabla_pacientes` varchar(64) DEFAULT NULL,
  `fecha_hora` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `envios`
--

LOCK TABLES `envios` WRITE;
/*!40000 ALTER TABLE `envios` DISABLE KEYS */;
/*!40000 ALTER TABLE `envios` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `log_envios`
--

DROP TABLE IF EXISTS `log_envios`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `log_envios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `envio_id` int(11) DEFAULT NULL,
  `paciente_id` int(11) DEFAULT NULL,
  `nombre_paciente` varchar(150) DEFAULT NULL,
  `numero_telefono` varchar(20) DEFAULT NULL,
  `mensaje` text DEFAULT NULL,
  `plantilla_clave` varchar(50) DEFAULT NULL,
  `estado_envio` enum('enviado','error','numero_invalido') NOT NULL,
  `respuesta` enum('pendiente','respondio','baja') DEFAULT 'pendiente',
  `whatsapp_message_id` varchar(255) DEFAULT NULL,
  `estado_whatsapp` enum('sent','delivered','read','failed') DEFAULT NULL,
  `descripcion_error` varchar(255) DEFAULT NULL,
  `area_id` int(11) DEFAULT NULL,
  `tabla_pacientes` varchar(64) DEFAULT NULL,
  `fecha_hora` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_envio` (`envio_id`),
  KEY `idx_paciente` (`paciente_id`),
  KEY `idx_log_area` (`area_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `log_envios`
--

LOCK TABLES `log_envios` WRITE;
/*!40000 ALTER TABLE `log_envios` DISABLE KEYS */;
/*!40000 ALTER TABLE `log_envios` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `pacientes_dev`
--

DROP TABLE IF EXISTS `pacientes_dev`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `pacientes_dev` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` varchar(150) NOT NULL,
  `apellido` varchar(150) NOT NULL DEFAULT '',
  `telefono` varchar(20) NOT NULL,
  `estado` enum('pendiente','enviado','error') NOT NULL DEFAULT 'pendiente',
  `whatsapp_opt_out` tinyint(1) NOT NULL DEFAULT 0,
  `respuesta_manual` varchar(12) DEFAULT NULL,
  `interesado` tinyint(1) NOT NULL DEFAULT 0,
  `fecha_actualizacion` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `pacientes_dev`
--

LOCK TABLES `pacientes_dev` WRITE;
/*!40000 ALTER TABLE `pacientes_dev` DISABLE KEYS */;
INSERT INTO `pacientes_dev` VALUES (1,'David','Araya','+56993921740','pendiente',0,NULL,0,'2026-09-28 10:49:49'),(2,'Sergio','Madariaga','+56941508435','pendiente',0,NULL,0,'2026-09-28 10:49:49');
/*!40000 ALTER TABLE `pacientes_dev` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `pacientes_prod`
--

DROP TABLE IF EXISTS `pacientes_prod`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `pacientes_prod` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `nombre` varchar(150) NOT NULL,
  `apellido` varchar(150) NOT NULL DEFAULT '',
  `telefono` varchar(20) NOT NULL,
  `estado` enum('pendiente','enviado','error') NOT NULL DEFAULT 'pendiente',
  `whatsapp_opt_out` tinyint(1) NOT NULL DEFAULT 0,
  `respuesta_manual` varchar(12) DEFAULT NULL,
  `interesado` tinyint(1) NOT NULL DEFAULT 0,
  `fecha_actualizacion` datetime DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `pacientes_prod`
--

LOCK TABLES `pacientes_prod` WRITE;
/*!40000 ALTER TABLE `pacientes_prod` DISABLE KEYS */;
INSERT INTO `pacientes_prod` VALUES (1,'David','Araya','+56993921740','pendiente',0,NULL,0,'2026-09-28 10:49:49'),(2,'Sergio','Madariaga','+56941508435','pendiente',0,NULL,0,'2026-09-28 10:49:49');
/*!40000 ALTER TABLE `pacientes_prod` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `password_resets`
--

DROP TABLE IF EXISTS `password_resets`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `password_resets` (
  `token` char(64) NOT NULL,
  `usuario` varchar(150) NOT NULL,
  `creado` datetime DEFAULT current_timestamp(),
  `expira` datetime NOT NULL,
  `usado` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`token`),
  KEY `idx_pr_usuario` (`usuario`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `password_resets`
--

LOCK TABLES `password_resets` WRITE;
/*!40000 ALTER TABLE `password_resets` DISABLE KEYS */;
/*!40000 ALTER TABLE `password_resets` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `roles_area`
--

DROP TABLE IF EXISTS `roles_area`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `roles_area` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `area_id` int(11) NOT NULL,
  `nombre` varchar(150) NOT NULL,
  `descripcion` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_area` (`area_id`),
  KEY `idx_rol_nombre` (`nombre`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `roles_area`
--

LOCK TABLES `roles_area` WRITE;
/*!40000 ALTER TABLE `roles_area` DISABLE KEYS */;
/*!40000 ALTER TABLE `roles_area` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `tarifas_whatsapp`
--

DROP TABLE IF EXISTS `tarifas_whatsapp`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `tarifas_whatsapp` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `pais` varchar(60) NOT NULL DEFAULT 'Chile',
  `moneda` varchar(8) DEFAULT 'USD',
  `marketing` decimal(12,6) DEFAULT NULL,
  `utility` decimal(12,6) DEFAULT NULL,
  `authentication` decimal(12,6) DEFAULT NULL,
  `service` decimal(12,6) DEFAULT NULL,
  `efectiva_desde` date DEFAULT NULL,
  `hash` char(64) NOT NULL,
  `fuente` varchar(255) DEFAULT NULL,
  `csv_texto` mediumtext DEFAULT NULL,
  `descargada` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_hash` (`hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `tarifas_whatsapp`
--

LOCK TABLES `tarifas_whatsapp` WRITE;
/*!40000 ALTER TABLE `tarifas_whatsapp` DISABLE KEYS */;
/*!40000 ALTER TABLE `tarifas_whatsapp` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `usuario_area_roles`
--

DROP TABLE IF EXISTS `usuario_area_roles`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `usuario_area_roles` (
  `usuario_id` int(11) NOT NULL,
  `rol_id` int(11) NOT NULL,
  `creado` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`usuario_id`,`rol_id`),
  KEY `idx_uer_rol` (`rol_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `usuario_area_roles`
--

LOCK TABLES `usuario_area_roles` WRITE;
/*!40000 ALTER TABLE `usuario_area_roles` DISABLE KEYS */;
/*!40000 ALTER TABLE `usuario_area_roles` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `usuarios`
--

DROP TABLE IF EXISTS `usuarios`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `usuarios` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `usuario` varchar(150) NOT NULL,
  `nombre` varchar(150) NOT NULL DEFAULT '',
  `rol` enum('usuario','supervisor','administrador','desarrollador') NOT NULL DEFAULT 'usuario',
  `permisos` varchar(500) NOT NULL DEFAULT '',
  `clave_hash` char(64) NOT NULL,
  `correo_recuperacion` varchar(150) NOT NULL DEFAULT '',
  `creado` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_usuario` (`usuario`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `usuarios`
--

LOCK TABLES `usuarios` WRITE;
/*!40000 ALTER TABLE `usuarios` DISABLE KEYS */;
INSERT INTO `usuarios` VALUES (1,'admin','Administrador','administrador','','240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9','','2026-09-28 10:49:49'),(2,'usuario','Usuario Final','usuario','mensajeria,historial,estadisticas,plantillas_editar','dfa7a2273567dcd1efffb9a46308e91c20fa13c44c3441bc69cd6a7869b3f7fd','','2026-09-28 10:49:49'),(3,'dev','Desarrollador','desarrollador','','87274af01876341455b32d805946f272871bb42effa6604dccf28bb027afa82b','','2026-09-28 10:49:49');
/*!40000 ALTER TABLE `usuarios` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Table structure for table `whatsapp_eventos`
--

DROP TABLE IF EXISTS `whatsapp_eventos`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!40101 SET character_set_client = utf8 */;
CREATE TABLE `whatsapp_eventos` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `clave` varchar(64) NOT NULL,
  `payload` text DEFAULT NULL,
  `recibido` datetime DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_clave` (`clave`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Dumping data for table `whatsapp_eventos`
--

LOCK TABLES `whatsapp_eventos` WRITE;
/*!40000 ALTER TABLE `whatsapp_eventos` DISABLE KEYS */;
/*!40000 ALTER TABLE `whatsapp_eventos` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Dumping routines for database 'snw_base'
--
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-09-28 11:17:20
